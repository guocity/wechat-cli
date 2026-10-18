import base64
import json
import shutil
import subprocess
import threading
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from wechat_cli.api.schemas import ChannelVideoInput
from wechat_cli.backends.macos import channels_publisher as publisher
from wechat_cli.core.errors import AppError


def mp4():
    def box(kind, data):
        return (len(data) + 8).to_bytes(4, "big") + kind + data

    return box(b"ftyp", b"isom\0\0\0\0isom") + box(b"moov", b"test") + box(b"mdat", b"test")


def payload(**extra):
    return ChannelVideoInput(dataBase64=base64.b64encode(mp4()).decode(), **extra).model_dump()


@pytest.mark.parametrize("encoded", ["!invalid", "", base64.b64encode(b"not mp4").decode()])
def test_invalid_video(encoded):
    with pytest.raises(AppError):
        publisher.decode_video(encoded)


def test_strict_inputs():
    for extra in [{"path": "/etc/passwd"}, {"title": "x" * 17}, {"description": "\0"}, {"waitSeconds": 121}]:
        with pytest.raises(ValidationError):
            payload(**extra)


def fake_backend(tmp_path, monkeypatch):
    monkeypatch.setattr(publisher, "load_runtime", lambda _: {"app": "/fake/WeChat.app"})
    return SimpleNamespace(directory=tmp_path, channels_lock=threading.Lock())


def test_compose_uploads_and_never_posts(tmp_path, monkeypatch):
    backend = fake_backend(tmp_path, monkeypatch)
    calls = []

    @contextmanager
    def page(app):
        def call(command):
            calls.append(command)
            return {"status": "ready", "sessionId": command.get("sessionId")}

        yield call

    result = publisher.operate(backend, payload(), page_factory=page)
    assert result["status"] == "ready"
    assert [c["action"] for c in calls] == ["begin", "chunk", "attach"]
    assert base64.b64decode(calls[1]["data"]) == mp4()
    assert len({c["sessionId"] for c in calls}) == 1
    assert backend.channels_lock.acquire(blocking=False)


def test_processing_does_not_click_publish(tmp_path, monkeypatch):
    backend = fake_backend(tmp_path, monkeypatch)
    calls = []

    @contextmanager
    def page(app):
        def call(command):
            calls.append(command)
            return {"status": "processing", "sessionId": command.get("sessionId")}

        yield call

    result = publisher.operate(backend, payload(waitSeconds=0), post=True, page_factory=page)
    assert result["status"] == "processing"
    assert not any(c["action"] == "post" for c in calls)


def test_post_once_after_ready_and_release_on_failure(tmp_path, monkeypatch):
    backend = fake_backend(tmp_path, monkeypatch)
    calls = []

    @contextmanager
    def page(app):
        def call(command):
            calls.append(command["action"])
            if command["action"] == "post":
                raise AppError("channels_operation_interrupted", "Uncertain")
            return {"status": "ready"}

        yield call

    with pytest.raises(AppError):
        publisher.operate(backend, payload(), post=True, page_factory=page)
    assert calls == ["begin", "chunk", "attach", "post"]
    assert backend.channels_lock.acquire(blocking=False)


def test_page_fixture():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node required for DOM state fixture")
    fixture = Path(__file__).with_name("channels-publisher-fixture.cjs")
    subprocess.run(
        [
            node,
            str(fixture),
            str(publisher.ASSETS / "channels-publisher.js"),
            str(publisher.ASSETS / "channels-options.js"),
        ],
        check=True,
    )


def test_api_capability_and_idempotency(tmp_path):
    from conftest import FakeNative, running_server

    native = FakeNative()
    with running_server(tmp_path, native) as (_, client, _):
        with pytest.raises(AppError) as error:
            client.channel_status()
        assert error.value.code == "native_feature_unavailable"
        native.capabilities = lambda: {"channelsPosting": "experimental"}
        native.channel_status = lambda: {"status": "empty"}
        calls = []
        native.compose_channel = lambda data, **extra: calls.append(data) or {"status": "ready"}
        assert client.channel_status()["status"] == "empty"
        with pytest.raises(AppError) as error:
            client.request("/api/channels/compose", method="POST", data=payload())
        assert error.value.code == "idempotency_key_required"
        first = client.compose_channel(payload(), idempotency_key="channel-test-001")
        assert client.compose_channel(payload(), idempotency_key="channel-test-001") == first
        assert len(calls) == 1
        assert json.loads(json.dumps(calls[0]))["description"] == ""


def test_local_file_source_is_mapped_and_preserves_filename(tmp_path):
    path = tmp_path / "roadster-ui.mp4"
    path.write_bytes(mp4())
    value = ChannelVideoInput(videoPath=str(path)).model_dump()
    with publisher.video_source(value) as (data, name):
        assert data[:] == mp4()
        assert name == path.name
    assert data.closed


def test_local_source_requires_exactly_one_absolute_path():
    for value in [{}, {"videoPath": "relative.mp4"}, {"videoPath": "/tmp/a.mp4", "dataBase64": "AAAA"}]:
        with pytest.raises(ValidationError):
            ChannelVideoInput(**value)


def test_local_file_size_bound_before_mapping(tmp_path, monkeypatch):
    path = tmp_path / "large.mp4"
    path.write_bytes(mp4())
    monkeypatch.setattr(publisher, "MAX_LOCAL_VIDEO", 1)
    with pytest.raises(AppError) as error:
        with publisher.video_source({"videoPath": str(path)}):
            pass
    assert error.value.code == "file_too_large"


def test_options_require_timezone_https_and_strict_boolean():
    from wechat_cli.api.schemas import ChannelOptions

    for value in [
        {"scheduleAt": "2026-10-17T12:00:00"},
        {"scheduleAt": "2026-10-17T12:00:01-04:00"},
        {"link": {"type": "article", "url": "http://example.com"}},
        {"link": {"type": "article", "url": "https://user:pass@example.com"}},
        {"declareOriginal": "true"},
    ]:
        with pytest.raises(ValidationError):
            ChannelOptions(**value)
    assert ChannelOptions(scheduleAt="2026-10-17T12:00:00-04:00").scheduleAt


def test_update_never_posts_and_originality_is_last():
    calls = []

    def call(command):
        calls.append(command)
        return {"status": "ready", "settings": {}, "validation": {}}

    publisher.apply_settings(
        call,
        "session",
        {
            "description": "caption",
            "options": {
                "location": "Park",
                "activity": "",
                "scheduleAt": "immediate",
                "declareOriginal": True,
            },
        },
    )
    actions = [c["action"] for c in calls]
    assert "post" not in actions and "attach" not in actions
    assert actions[-2:] == ["options-original", "status"]
    assert (
        next(c for c in calls if c.get("kind") == "activity" and c["action"] == "options-select")["value"]
        == "不参与活动"
    )


def test_update_stops_at_existing_dialog():
    def call(command):
        assert command["action"] == "status"
        return {"blockedByDialog": True}

    with pytest.raises(AppError) as error:
        publisher.apply_settings(call, "session", {"description": "caption"})
    assert error.value.code == "channels_dialog_open"


def test_upload_opens_composer_and_cleans_connections_before_retry(monkeypatch):
    events = []

    @contextmanager
    def page(app, *, authenticate=False, authorize=None):
        assert authenticate
        events.append("scan")
        try:
            if events.count("scan") == 1:
                raise AppError("channels_page_not_found", "closed")
            if events.count("scan") == 2:
                raise AppError("channels_form_changed", "loading")
            yield lambda command: {"status": "empty"}
        finally:
            events.append("cleanup")

    monkeypatch.setattr(publisher, "publishing_page", page)
    monkeypatch.setattr(publisher, "open_channels_entry", lambda backend: events.append("open"))
    monkeypatch.setattr(publisher.time, "sleep", lambda seconds: None)
    with publisher.upload_page(object(), "/app") as call:
        assert call({"action": "status"}) == {"status": "empty"}
    assert events == ["scan", "cleanup", "open", "scan", "cleanup", "scan", "cleanup"]


def test_ambiguous_page_is_never_navigated_or_reopened(monkeypatch):
    @contextmanager
    def page(*args, **kwargs):
        raise AppError("channels_page_ambiguous", "multiple")
        yield

    monkeypatch.setattr(publisher, "publishing_page", page)
    monkeypatch.setattr(publisher, "open_channels_entry", lambda backend: pytest.fail("must not open"))
    with pytest.raises(AppError, match="multiple"):
        with publisher.upload_page(object(), "/app"):
            pytest.fail("must not yield")


def native_page_fixture(tmp_path, monkeypatch, *, oauth=False, value=None, workers=(1,)):
    frida = pytest.importorskip("frida")
    from contextlib import nullcontext

    app = tmp_path / "Research.app"
    renderer = (app / publisher.RENDERER).resolve()
    renderer.parent.mkdir(parents=True)
    renderer.with_name(publisher.HELPER).touch()
    renderer.with_name("g.config").write_text(
        json.dumps(
            {
                "code_signing": "required",
                "interaction": {"type": "listen", "address": "127.0.0.1", "port": 45200, "token": "a" * 64},
            }
        )
    )
    calls = []

    def evaluate(context, source):
        calls.append((context, source))
        if source in {publisher.IDENTIFY, publisher.MANAGER_IDENTIFY}:
            return json.dumps({"match": not oauth})
        if source == publisher.OAUTH_IDENTIFY:
            return json.dumps(oauth)
        if source == publisher.PORTAL_IDENTIFY:
            return "false"
        return json.dumps(value)

    rpc = SimpleNamespace(
        identity=lambda: {"pid": 123, "path": str(renderer), "policy": "required"},
        workers=lambda: list(workers),
        evaluate=evaluate,
    )
    script = SimpleNamespace(exports_sync=rpc, load=lambda: None, unload=lambda: None)
    session = SimpleNamespace(create_script=lambda _: script, detach=lambda: None)
    manager = SimpleNamespace(
        add_remote_device=lambda *a, **k: SimpleNamespace(attach=lambda _: session),
        remove_remote_device=lambda _: None,
    )

    def connect(address, **kwargs):
        if address[1] != 45200:
            raise OSError()
        return nullcontext()

    monkeypatch.setattr(publisher, "process_snapshot", lambda: f"123 {renderer}")
    monkeypatch.setattr(publisher.socket, "create_connection", connect)
    monkeypatch.setattr(frida, "get_device_manager", lambda: manager)
    return app, calls


def test_oauth_document_transition_is_retryable_before_upload(tmp_path, monkeypatch):
    app, _ = native_page_fixture(tmp_path, monkeypatch, oauth=True, value=True)
    with pytest.raises(AppError) as error:
        with publisher.publishing_page(app, authenticate=True):
            pytest.fail("must wait for the document")
    assert error.value.code == "channels_authenticating"


@pytest.mark.parametrize(
    "action,code", [("status", "channels_form_changed"), ("begin", "channels_operation_interrupted")]
)
def test_non_object_native_results_have_explicit_outcomes(tmp_path, monkeypatch, action, code):
    app, _ = native_page_fixture(tmp_path, monkeypatch, value=None)
    with publisher.publishing_page(app) as call:
        with pytest.raises(AppError) as error:
            call({"action": action})
    assert error.value.code == code


def test_multiple_management_views_reuse_one_without_navigating(tmp_path, monkeypatch):
    app, calls = native_page_fixture(tmp_path, monkeypatch, value={"status": "ready"}, workers=(1, 2))
    with publisher.publishing_page(app, target="manager") as call:
        assert call({"action": "status"})["status"] == "ready"
    assert calls[-1][0] == 1
    assert all("location.assign" not in source for _, source in calls)


@pytest.mark.parametrize("accept", [False, True])
def test_originality_terms_require_explicit_acceptance(monkeypatch, accept):
    calls = []
    blocked = [True]
    monkeypatch.setattr(publisher.time, "sleep", lambda _: None)

    def call(command):
        calls.append(command)
        if command["action"] == "options-original-confirm":
            assert command["acceptTerms"] is True
            blocked[0] = False
        return {
            "blockedByDialog": blocked[0],
            "status": "action_required" if blocked[0] else "ready",
            "settings": {"declareOriginal": True},
        }

    result = publisher.apply_settings(
        call,
        "session",
        {"options": {"declareOriginal": True, "acceptOriginalityTerms": accept, "location": None}},
    )
    assert any(c["action"] == "options-original-confirm" for c in calls) is accept
    assert result["blockedByDialog"] is not accept
    assert all(c["action"] != "post" for c in calls)
