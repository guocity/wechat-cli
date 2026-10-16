"""Native media identity, decoder boundaries and windowless endpoint regression."""

import hashlib
import json
import threading
import time
from types import SimpleNamespace

import pytest

from wechat_cli.backends.macos import channels_native as native
from wechat_cli.backends.macos.backend import MacOSBackend
from wechat_cli.backends.macos.channels_decode import decrypt
from wechat_cli.core.errors import AppError
from wechat_cli.core.models import channels_descriptor, resolve_message


def card():
    return resolve_message(
        {
            "id": "42",
            "type": 49,
            "content": "<msg><appmsg><type>51</type><finderFeed><objectId>123</objectId>"
            "<objectNonceId>native-nonce</objectNonceId></finderFeed></appmsg></msg>",
        }
    )


def descriptor(**changes):
    return {
        "objectId": "123",
        "url": "https://finder.video.qq.com/video?encfilekey=test",
        "urlToken": "&token=test",
        "decodeKey": "1",
        "format": "h264",
        **changes,
    }


def mp4():
    body = bytes(range(256)) * 600
    return (
        b"\0\0\0\x10ftypisom\0\0\0\0"
        + b"\0\0\0\x08moov"
        + (len(body) + 8).to_bytes(4, "big")
        + b"mdat"
        + body
    )


def test_isaac_word_order_block_boundary_and_clear_tail():
    assert decrypt(bytes(32), 1).hex() == "e19ed5d2ca98af2da7a18d07cab39b52a0ab0232d180af1472b36dcf1af5e46f"
    assert (
        hashlib.sha256(decrypt(bytes(4097), 1)).hexdigest()
        == "222b71eafad1ce2ac64915821b4db0520f3e870e938e78444f6e78a5b8aa3588"
    )
    clear = mp4()
    encrypted = decrypt(clear, 1)
    assert encrypted[131072:] == clear[131072:]
    assert decrypt(encrypted, 1) == clear
    assert decrypt(b"123", 1) == decrypt(b"123" + bytes(5), 1)[:3]


def test_download_decrypts_native_key_and_preserves_filename():
    clear = mp4()

    def downloader(url, **options):
        assert "token=test" in url and "X-snsvideoflag=h264" in url
        assert options == {"maximum": 128 * 1024**2, "with_name": True}
        return decrypt(clear, 1), "native.mp4"

    assert native.decode_download(descriptor(), "123", downloader) == (clear, "native.mp4")
    with pytest.raises(AppError, match="valid MP4"):
        native.decode_download(descriptor(decodeKey="2"), "123", downloader)


@pytest.mark.parametrize(
    "changes",
    [
        {"objectId": "456"},
        {"objectId": 123},
        {"decodeKey": "18446744073709551616"},
        {"decodeKey": 1},
        {"urlToken": "missing-delimiter"},
        {"urlToken": "&token=\nsecret"},
        {"url": "https://127.0.0.1/private"},
        {"format": "../../other"},
    ],
)
def test_invalid_or_unrelated_metadata_never_downloaded(changes):
    with pytest.raises(AppError):
        native.decode_download(
            descriptor(**changes),
            "123",
            lambda *a, **kw: pytest.fail("Unvalidated metadata reached downloader"),
        )


def test_worker_callback_is_cancelled_after_success_and_failure():
    for outcome in (descriptor(), {"error": "native_media_identity_mismatch"}):
        calls = []

        def evaluate(worker, source):
            assert worker == 8
            request = json.loads(source.rsplit(")(", 1)[1][:-2])
            calls.append(request)
            if request["operation"] == "identify":
                return '{"finder":true}'
            if request["operation"] == "start":
                assert request["objectId"] == "123" and request["nonce"] == "native-nonce"
                return '{"started":true}'
            if request["operation"] == "poll":
                return json.dumps(outcome)
            return '{"cancelled":true}'

        reader = SimpleNamespace(workers=lambda: [8], evaluate=evaluate)
        if "error" in outcome:
            with pytest.raises(AppError):
                native._resolve(reader, {"objectId": "123", "nonce": "native-nonce"}, time.monotonic() + 1)
        else:
            assert (
                native._resolve(reader, {"objectId": "123", "nonce": "native-nonce"}, time.monotonic() + 1)
                == outcome
            )
        assert calls[-1]["operation"] == "cancel"
        assert calls[-1]["id"] == calls[1]["id"]


def test_media_endpoint_uses_native_worker_without_ui_or_unauthenticated_download(tmp_path, monkeypatch):
    from wechat_cli.backends.macos import setup
    from wechat_cli.core import media

    backend = MacOSBackend.__new__(MacOSBackend)
    backend.directory = tmp_path
    backend.channels_lock = threading.Lock()
    backend.self_id = lambda: "account"
    # No controls member exists: any native viewer/UI fallback must fail this test.
    monkeypatch.setattr(setup, "load_runtime", lambda directory: {"app": "/approved/app"})
    monkeypatch.setattr(media, "read_channels", lambda *a: pytest.fail("Unexpected unauthenticated download"))
    calls = []

    def download(app, message):
        assert app == "/approved/app" and channels_descriptor(message)["objectNonceId"] == "native-nonce"
        calls.append(message["id"])
        return mp4(), "source.mp4"

    monkeypatch.setattr(native, "download", download)
    assert backend.channels_video(card()) == (mp4(), "video/mp4", "source.mp4")
    assert backend.channels_video(card())[0] == mp4()
    assert calls == ["42"]


def test_shipped_worker_protocol_restores_callbacks_and_rejects_other_objects():
    import shutil
    import subprocess
    from pathlib import Path

    node = shutil.which("node")
    if not node:
        pytest.skip("Node is needed for the synthetic Finder worker fixture")
    subprocess.run(
        [
            node,
            str(Path(__file__).with_name("channels-resolver-fixture.js")),
            str(native.ASSETS / "channels-resolver.js"),
        ],
        check=True,
        timeout=10,
    )
