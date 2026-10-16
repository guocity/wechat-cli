import json
import time
from urllib.parse import urlencode

from wechat_cli.backends.macos.channels import HEADER, clear_header, playback_urls, recover_reader


def url(**extra):
    return "https://finder.video.qq.com/251/20302/stodownload?" + urlencode(
        {"encfilekey": "card-file-key", "m": "card-digest", **extra}
    )


def mp4():
    body = bytes(range(256)) * 600
    return (
        b"\0\0\0\x10ftypisom\0\0\0\0"
        + b"\0\0\0\x08moov"
        + (len(body) + 8).to_bytes(4, "big")
        + b"mdat"
        + body
    )


class Memory:
    def __init__(self, data):
        self.data = data

    def ranges(self):
        return [{"base": "0x1000", "size": len(self.data)}]

    def read(self, address, size):
        start = int(address, 16) - 4096
        return self.data[start : start + size]


def test_playback_url_requires_the_requested_file_and_authenticated_cdn():
    good = url(token="signed")
    text = json.dumps({"url": good})
    assert playback_urls(text, url()) == [good]
    for bad in [
        url(token="signed", m="other-card"),
        url(token="signed", encfilekey="other-file"),
        url(),
        good.replace("finder.video.qq.com", "127.0.0.1"),
    ]:
        assert playback_urls(json.dumps({"url": bad}), url()) == []


def test_recovered_prefix_must_match_this_exact_download():
    clear = mp4()
    encrypted = b"X" * HEADER + clear[HEADER:]
    memory = Memory(clear)
    assert clear_header(memory, 4096, encrypted) == clear
    wrong = encrypted[:HEADER] + b"other-video" * 100
    assert clear_header(memory, 4096, wrong) is None
    assert clear_header(Memory(b"broken" + clear[6:]), 4096, encrypted) is None


def test_recovery_uses_signed_url_and_verified_header():
    clear = mp4()
    encrypted = b"X" * HEADER + clear[HEADER:]
    memory = Memory(json.dumps({"url": url(token="signed")}).encode().ljust(4096, b"\0") + clear)
    requests = []

    def download(candidate, **options):
        requests.append(candidate)
        assert options["maximum"] == 128 * 1024**2
        return encrypted, "original.mp4"

    assert recover_reader(memory, url(), deadline=time.monotonic() + 5, downloader=download) == (
        clear,
        "original.mp4",
    )
    assert requests == [url(token="signed")]


def test_unrelated_buffer_cannot_supply_video_header():
    clear = mp4()
    encrypted = b"X" * HEADER + clear[HEADER:]
    wrong = clear[:HEADER] + b"other-video" * 100
    memory = Memory(json.dumps({"url": url(token="signed")}).encode().ljust(4096, b"\0") + wrong)
    assert (
        recover_reader(
            memory, url(), deadline=time.monotonic() + 5, downloader=lambda *a, **k: (encrypted, None)
        )
        is None
    )


def test_deadline_prevents_unbounded_memory_search():
    memory = Memory(mp4())
    assert recover_reader(memory, url(), deadline=time.monotonic() - 1) is None


def test_cache_preserves_filename_and_is_scoped_to_account(tmp_path, monkeypatch):
    import threading

    from wechat_cli.backends.macos.backend import MacOSBackend
    from wechat_cli.core import media
    from wechat_cli.core.models import resolve_message

    native = MacOSBackend.__new__(MacOSBackend)
    native.directory = tmp_path
    native.channels_lock = threading.Lock()
    account = ["first-account"]
    native.self_id = lambda: account[0]
    calls = []

    def download(message, variant):
        calls.append(account[0])
        return mp4(), "video/mp4", "original-name.mp4"

    monkeypatch.setattr(media, "read_channels", download)
    message = resolve_message(
        {"id": "42", "type": 49, "content": "<msg><appmsg><type>51</type></appmsg></msg>"}
    )
    assert native.channels_video(message)[2] == "original-name.mp4"
    assert native.channels_video(message)[2] == "original-name.mp4"
    assert calls == ["first-account"]
    account[0] = "second-account"
    assert native.channels_video(message)[0] == mp4()
    assert calls == ["first-account", "second-account"]
    assert len(list((tmp_path / "channels-cache").glob("*.mp4"))) == 2
    assert all(p.stat().st_mode & 0o077 == 0 for p in (tmp_path / "channels-cache").glob("*"))


def test_native_opener_uses_resolved_chat_caption_and_author(tmp_path):
    from wechat_cli.backends.macos.controls import Controls

    controls = Controls(tmp_path)
    calls = []
    controls.invoke = lambda action, **values: calls.append((action, values))
    message = {
        "id": "42",
        "chatId": "chat",
        "kind": "channels_video",
        "details": {"description": "Exact caption", "author": "Author"},
    }

    class Native:
        def chats(self):
            return {"items": [{"id": "chat", "displayName": "Selected chat"}]}

        def messages(self, chat_id, limit):
            assert (chat_id, limit) == ("chat", 100)
            return {"items": [message]}

    controls.open_channels(Native(), message)
    assert calls == [
        ("channels.open", {"chatName": "Selected chat", "caption": "Exact caption", "author": "Author"})
    ]


def test_native_opener_rejects_ambiguous_cards_before_ui_actions(tmp_path):
    import pytest

    from wechat_cli.backends.macos.controls import Controls
    from wechat_cli.core.errors import AppError

    controls = Controls(tmp_path)
    controls.invoke = lambda *a, **kw: pytest.fail("Ambiguous card must not trigger native input")
    message = {
        "id": "42",
        "chatId": "chat",
        "kind": "channels_video",
        "details": {"description": "Same caption", "author": "Author"},
    }

    class Native:
        def messages(self, *args):
            return {"items": [message, {**message, "id": "43"}]}

    with pytest.raises(AppError, match="Multiple Channels cards"):
        controls.open_channels(Native(), message)


def test_cards_without_optional_digest_still_match_exact_encrypted_file():
    original = "https://finder.video.qq.com/251/20302/stodownload?encfilekey=exact-file"
    signed = original + "&token=signed"
    assert playback_urls(json.dumps({"url": signed}), original) == [signed]
    assert playback_urls(json.dumps({"url": signed.replace("exact-file", "other-file")}), original) == []
    assert playback_urls(json.dumps({"url": signed.replace("/20302/", "/20304/")}), original) == []
    assert playback_urls(json.dumps({"url": original}), original) == []


def test_standalone_native_url_is_correlated_without_json_metadata():
    signed = url(token="signed")
    assert playback_urls("native playback: " + signed + "\0next allocation", url()) == [signed]
    assert playback_urls(signed.replace("card-digest", "wrong-digest"), url()) == []


def test_recovery_rediscovers_replaced_renderer_and_tolerates_cleanup_race(tmp_path, monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace

    import frida

    from wechat_cli.backends.macos import channels

    expected = tmp_path / channels.RENDERER
    expected.parent.mkdir(parents=True)
    (expected.parent / "g.config").write_text(
        json.dumps(
            {
                "code_signing": "required",
                "interaction": {"type": "listen", "address": "127.0.0.1", "port": 45200, "token": "a" * 64},
            }
        )
    )
    generation = [0]
    scans, detached, removed = [], [], []

    def snapshot():
        generation[0] += 1
        return f"{generation[0]} {expected}"

    class Script:
        def __init__(self):
            self.pid = generation[0]
            self.exports_sync = SimpleNamespace(
                identity=lambda: {
                    "pid": self.pid,
                    "path": str(expected),
                    "policy": "required",
                }
            )

        def load(self):
            pass

        def unload(self):
            raise frida.InvalidOperationError("Renderer exited while detaching")

    class Session:
        def create_script(self, source):
            self.pid = generation[0]
            return Script()

        def detach(self):
            detached.append(self.pid)

    manager = SimpleNamespace(
        add_remote_device=lambda *a, **kw: SimpleNamespace(attach=lambda name: Session()),
        remove_remote_device=lambda endpoint: removed.append(endpoint),
    )

    def connect(address, **kwargs):
        if address[1] != 45200:
            raise OSError()
        return nullcontext()

    def recover(reader, *args, **kwargs):
        scans.append(reader.identity()["pid"])
        return (mp4(), None) if generation[0] == 2 else None

    monkeypatch.setattr(channels, "process_snapshot", snapshot)
    monkeypatch.setattr(channels.socket, "create_connection", connect)
    monkeypatch.setattr(channels.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(channels, "recover_reader", recover)
    monkeypatch.setattr(frida, "get_device_manager", lambda: manager)
    prepared = []
    assert channels.recover(tmp_path, url(), prepare=lambda: prepared.append(True)) == (mp4(), None)
    assert prepared == [True]
    assert scans == [1, 2]
    assert detached == [1, 2]
    assert removed == ["127.0.0.1:45200"] * 2
