import base64
import io
import shutil
import threading
from types import SimpleNamespace

import pytest
from PIL import Image

from wechat_cli.api.schemas import MomentInput
from wechat_cli.backends.macos.moments import moment_action, prepare_moment, stage_media
from wechat_cli.core.errors import AppError


def photo():
    output = io.BytesIO()
    Image.new("RGB", (8, 8), "green").save(output, "PNG")
    return {"dataBase64": base64.b64encode(output.getvalue()).decode()}


def payload(count=1):
    return {"text": "Test caption ☺", "kind": "images", "media": [photo() for _ in range(count)]}


def test_nine_photos_are_private_ordered_files(tmp_path):
    folder = stage_media(tmp_path, payload(9))
    assert sorted(p.name for p in folder.iterdir()) == [f"{n:02}.png" for n in range(1, 10)]
    assert folder.stat().st_mode & 0o777 == 0o700
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in folder.iterdir())


@pytest.mark.parametrize("value", [payload(0), payload(10), {"kind": "video", "media": [photo(), photo()]}])
def test_invalid_media_counts(tmp_path, value):
    with pytest.raises(AppError, match="Choose"):
        stage_media(tmp_path, value)


@pytest.mark.parametrize("encoded", ["not base64!", base64.b64encode(b"not an image").decode()])
def test_bad_images_leave_no_staged_files(tmp_path, encoded):
    with pytest.raises(AppError):
        stage_media(tmp_path, {"kind": "images", "media": [{"dataBase64": encoded}]})
    assert not list((tmp_path / "outgoing-moments").glob("moment-*"))


def test_unknown_fields_and_nul_caption_rejected():
    from pydantic import ValidationError

    for value in [{**payload(), "path": "/tmp/file"}, {**payload(), "text": "a\0b"}]:
        with pytest.raises(ValidationError):
            MomentInput.model_validate(value)


def control_fixture(tmp_path, invoke):
    return SimpleNamespace(directory=tmp_path, lock=threading.Lock(), moment_session=None, invoke=invoke)


def test_video_inspection_precedes_ui_and_cleans_invalid_video(tmp_path):
    calls = []

    def invoke(action, **extra):
        calls.append(action)
        if action == "moments.ready":
            return {"ready": True}
        raise AppError("moment_invalid_video", "Video exceeds 30 seconds")

    controls = control_fixture(tmp_path, invoke)
    with pytest.raises(AppError):
        prepare_moment(controls, {"text": "", "kind": "video", "media": [photo()]})
    assert calls == ["moments.ready", "moments.inspect"]
    assert not list((tmp_path / "outgoing-moments").glob("moment-*"))
    assert controls.lock.acquire(blocking=False)


def test_prepare_never_posts_and_retains_async_media(tmp_path):
    calls = []

    def invoke(action, **extra):
        calls.append(action)
        return {"source": "native", "status": "composing"}

    controls = control_fixture(tmp_path, invoke)
    result = prepare_moment(controls, payload())
    assert calls == ["moments.ready", "moments.compose"]
    assert result["sessionId"] == controls.moment_session
    assert result["acknowledgedByServer"] is False
    assert len(list((tmp_path / "outgoing-moments").glob("moment-*"))) == 1
    with pytest.raises(AppError) as caught:
        prepare_moment(controls, payload())
    assert caught.value.code == "moment_composer_busy"


def test_uncertain_post_cannot_be_retried_under_new_key(tmp_path):
    def invoke(action, **extra):
        raise AppError("native_control_failed", "Interrupted")

    controls = control_fixture(tmp_path, invoke)
    controls.moment_session = "session"
    with pytest.raises(AppError):
        moment_action(controls, "session", "post")
    assert controls.moment_session is None
    with pytest.raises(AppError) as caught:
        moment_action(controls, "session", "post")
    assert caught.value.code == "moment_session_not_found"


def test_closed_native_draft_does_not_block_new_composition(tmp_path):
    calls = []

    def invoke(action, **extra):
        calls.append(action)
        if action == "moments.status":
            return {"source": "native", "status": "closed"}
        return {"source": "native", "status": "composing"}

    controls = control_fixture(tmp_path, invoke)
    controls.moment_session = "closed-session"
    result = prepare_moment(controls, payload())
    assert result["sessionId"] != "closed-session"
    assert calls == ["moments.status", "moments.ready", "moments.compose"]


def test_preclick_failure_preserves_session(tmp_path):
    def invoke(action, **extra):
        error = AppError("moment_finish_native_selection", "Finish selecting contacts")
        error.possible_send = False
        raise error

    controls = control_fixture(tmp_path, invoke)
    controls.moment_session = "session"
    with pytest.raises(AppError):
        moment_action(controls, "session", "post")
    assert controls.moment_session == "session"


def test_staging_capacity_is_bounded(tmp_path):
    for _ in range(8):
        stage_media(tmp_path, payload())
    with pytest.raises(AppError) as caught:
        stage_media(tmp_path, payload())
    assert caught.value.code == "moment_staging_full"
    shutil.rmtree(tmp_path / "outgoing-moments")


def test_cancel_releases_staged_media_after_native_confirmation(tmp_path):
    controls = control_fixture(tmp_path, lambda action, **extra: {"status": "cancelled"})
    controls.moment_session = "session"
    controls.moment_folder = stage_media(tmp_path, payload())
    folder = controls.moment_folder
    moment_action(controls, "session", "cancel")
    assert not folder.exists()
    assert controls.moment_folder is None


def test_api_routes_are_idempotent_and_platform_gated(api):
    _, client, native = api
    with pytest.raises(AppError) as caught:
        client.compose_moment(payload(), idempotency_key="moment-test-001")
    assert caught.value.code == "native_feature_unavailable"
    native.capabilities = lambda: {"momentsPosting": "wechat_native_ui"}
    calls = []

    def compose(value):
        calls.append(value)
        return {"status": "composing", "sessionId": "11111111-1111-1111-1111-111111111111"}

    native.compose_moment = compose
    result = client.compose_moment(payload(), idempotency_key="moment-test-002")
    assert client.compose_moment(payload(), idempotency_key="moment-test-002") == result
    assert len(calls) == 1
    native.moment_action = lambda session, action: {"status": "submitted", "acknowledgedByServer": False}
    assert (
        client.moment_action(result["sessionId"], "post", idempotency_key="moment-test-003")["status"]
        == "submitted"
    )


def test_cli_photo_and_video_options_are_exclusive():
    from wechat_cli.cli import parser

    args = parser().parse_args(["moment", "--image", "a.jpg", "--image", "b.png", "--text", "Hi"])
    assert [p.name for p in args.image] == ["a.jpg", "b.png"]
    assert args.post is False
    with pytest.raises(SystemExit):
        parser().parse_args(["moment", "--image", "a.jpg", "--video", "v.mp4"])


def test_moments_listing_is_read_only_and_platform_gated(api):
    _, client, native = api
    with pytest.raises(AppError) as caught:
        client.moments()
    assert caught.value.code == "native_feature_unavailable"
    native.capabilities = lambda: {"momentsReading": "wechat_native_ui"}
    limits = []
    native.moments = lambda limit: limits.append(limit) or {"items": [{"displayText": "Native post"}]}
    assert client.moments(25)["items"] == [{"displayText": "Native post"}]
    assert limits == [25]
    for limit in [0, 1001, "bad"]:
        with pytest.raises(AppError):
            client.moments(limit)
    assert limits == [25]


def test_cli_moments_all_and_limit():
    from wechat_cli.cli import parser

    args = parser().parse_args(["moments", "--all"])
    assert args.all is True
    assert parser().parse_args(["moments", "--limit", "50"]).limit == 50
    with pytest.raises(SystemExit):
        parser().parse_args(["moments", "--limit", "1001"])
