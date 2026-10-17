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
