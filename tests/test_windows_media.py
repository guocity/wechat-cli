"""Account boundaries and binary-format validation for the Windows media path."""

import base64
import hashlib
import struct
import zlib

import pytest

from wechat_cli.backends.windows.backend import WindowsBackend
from wechat_cli.backends.windows.images import dimensions
from wechat_cli.backends.windows.media_cache import MediaCache
from wechat_cli.backends.windows.protobuf import field, fields
from wechat_cli.core.errors import AppError


def png(width=10, height=20):
    def chunk(name, value):
        return struct.pack(">I", len(value)) + name + value + struct.pack(">I", zlib.crc32(name + value))

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\0" * (width * 3 + 1) * height))
        + chunk(b"IEND", b"")
    )


def test_windows_dimensions_without_pillow():
    image = png()
    assert dimensions(image) == {"width": 10, "height": 20}
    for invalid in [image[:-1], image + b"junk", image[:30] + b"!" + image[31:], b"GIF89a"]:
        with pytest.raises(AppError) as error:
            dimensions(invalid)
        assert error.value.code == "invalid_image_format"
    with pytest.raises(AppError) as error:
        dimensions(png(5000, 4000))
    assert error.value.code == "image_dimensions_unsupported"


def test_wire_codec_bounds_and_unicode():
    value = "微信 📨".encode()
    assert list(fields(field(1, 2**64 - 1) + field(2, value))) == [(1, 2**64 - 1), (2, value)]
    for invalid in [b"\x00", b"\x0b", b"\x12\x05a", b"\x08" + b"\xff" * 10, b"\x08\x80"]:
        with pytest.raises(ValueError):
            list(fields(invalid))


def test_cache_does_not_follow_account_escape(tmp_path):
    root = tmp_path / "account"
    (root / "msg").mkdir(parents=True)
    outside = tmp_path / "other-account"
    outside.mkdir()
    (outside / "image.dat").write_bytes(b"private")
    (root / "msg/escape").symlink_to(outside, target_is_directory=True)
    cache = MediaCache(None)
    assert list(cache.matching_paths(root, {"image.dat"})) == []
    for name in ["../image.dat", "C:secret", "a\\b", ".."]:
        with pytest.raises(AppError):
            list(cache.matching_paths(root, {name}))


def test_versioned_image_cache_lengths_and_native_key_boundary():
    class Native:
        def _request(self, method, params):
            assert method == "decodeImageHeader"
            assert params == {"version": 2, "bytes": list(b"x" * 16)}
            return {"bytes": list(b"GIF89a"), "xor": 23}

    cache = MediaCache(Native())
    data = (
        b"\x07\x08V2\x08\x07"
        + struct.pack("<II", 6, 3)
        + b"\x01"
        + b"x" * 16
        + b"middle"
        + bytes(c ^ 23 for c in b"end")
    )
    assert cache.decode(data) == b"GIF89amiddleend"
    for invalid in [data[:20], data[:6] + struct.pack("<II", 2**32 - 1, 1) + data[14:]]:
        with pytest.raises(AppError):
            cache.decode(invalid)


def test_attachment_verifies_digest_and_deduplicates_copies(tmp_path):
    root = tmp_path / "account"
    (root / "msg/a").mkdir(parents=True)
    (root / "msg/b").mkdir()
    binary = b"fixture"
    digest = hashlib.md5(binary).hexdigest()
    for folder in ["a", "b"]:
        (root / "msg" / folder / "fixture.txt").write_bytes(binary)

    class Native:
        def _query(self, *args):
            assert args[2] == [digest]
            return [{"file_name": "fixture.txt"}]

        def _request(self, *args):
            return {"root": str(root), "account": "account"}

        def self_id(self):
            return "account"

    message = {"details": {"md5": digest, "sizeBytes": len(binary)}}
    result = MediaCache(Native()).attachment(message)
    assert result == {"files": [{"dataBase64": base64.b64encode(binary).decode()}]}
    for path in root.rglob("*.txt"):
        path.write_bytes(b"altered")
    assert MediaCache(Native()).attachment(message) == {"files": []}


@pytest.mark.parametrize("name", ["../secret", "C:file", "CON.txt", "nul", "a.", "a ", "x|y", "a\0b"])
def test_staging_rejects_unsafe_windows_names(tmp_path, name):
    with pytest.raises(AppError):
        WindowsBackend(tmp_path).stage_outgoing(name, b"fixture")
    assert not (tmp_path / "outgoing").exists()


def test_staging_retains_source_for_async_native_upload(tmp_path):
    backend = WindowsBackend(tmp_path)
    path = backend.stage_outgoing("文件.txt", b"fixture")
    assert path.read_bytes() == b"fixture"
    assert path.is_relative_to(tmp_path / "outgoing")
    assert path.name == "文件.txt"


def test_windows_attachment_digest_can_be_on_appmsg():
    from wechat_cli.core.models import resolve_message

    digest = "a" * 32
    result = resolve_message(
        {
            "type": 49,
            "subType": 6,
            "content": f"<msg><appmsg><title>fixture.txt</title><type>6</type><md5>{digest}</md5>"
            "<appattach><totallen>7</totallen><fileext>txt</fileext></appattach></appmsg></msg>",
        }
    )
    assert result["details"]["md5"] == digest
    assert result["details"]["sizeBytes"] == 7


@pytest.mark.skipif(__import__("sys").platform != "win32", reason="Windows CryptoAPI")
def test_windows_native_aes_cbc_and_ecb():
    from wechat_cli.backends.windows.crypto import decrypt

    key = bytes(range(16))
    plain = b"GIF89a Windows AES media fixture"
    fixtures = [
        (
            bytes(16),
            "943a41fb9cf2bd4691bcce55637f058d140177385d099afb7e43c291cb65decf0cf0e4de625e40afa745eb4a46699cce",
        ),
        (
            key,
            "98f476ffe3b13a1d389c644674ff2bda4909b4d0d286d947130f154d2d46c41744d515059e011d871aefa4e7043e1d61",
        ),
        (
            None,
            "943a41fb9cf2bd4691bcce55637f058dfebf580b7287a4015cfbc269623b27d3954f64f2e4e86e9eee82d20216684899",
        ),
    ]
    for iv, encrypted in fixtures:
        assert decrypt(bytes.fromhex(encrypted), key, iv) == plain
    assert decrypt(b"truncated", key) is None


def test_voice_cache_accepts_pre_ack_row_but_keeps_message_scope(tmp_path, monkeypatch):
    backend = WindowsBackend(tmp_path)
    monkeypatch.setattr(backend, "_request", lambda _: ["media_0.db"])
    payload = b"\x02#!SILK_V3" + b"fixture"

    def query(database, sql, params, maximum):
        assert database == "media_0.db"
        assert "(v.svr_id=? OR v.svr_id=0)" in sql
        assert params == ["chat", "42", "9007199254740993", 123]
        assert maximum == 2
        return [{"voice_data": {"hex": payload.hex()}}]

    monkeypatch.setattr(backend, "_query", query)
    assert (
        backend.voice_bytes({"chatId": "chat", "id": "42", "serverId": "9007199254740993", "timestamp": 123})
        == payload
    )


def test_owned_ocr_helper_requires_registered_parent_and_exact_path(tmp_path, monkeypatch):
    from wechat_cli.backends.windows import processes

    exe = tmp_path / "Weixin.exe"
    child = {"Id": 2, "Path": str(exe), "parent": 99, "type": "wxocr"}
    monkeypatch.setattr(processes, "process_snapshot", lambda: [child])
    assert processes.main_processes() == [child]
    processes._OWNED_HELPER_PARENTS[99] = exe
    try:
        assert processes.main_processes() == []
        child["Path"] = str(tmp_path / "other/Weixin.exe")
        assert processes.main_processes() == [child]
    finally:
        processes._OWNED_HELPER_PARENTS.pop(99)


def test_ocr_registers_real_interpreter_before_allowing_native_launch(tmp_path, monkeypatch):
    import json
    from pathlib import Path

    from wechat_cli.backends.windows import backend as module
    from wechat_cli.backends.windows import processes

    backend = WindowsBackend(tmp_path)
    backend.executable = tmp_path / "Weixin.exe"
    main = {"Id": 7, "Path": str(backend.executable), "parent": 1, "type": None}
    snapshot = [main]
    monkeypatch.setattr(processes, "process_snapshot", lambda: snapshot)

    class Launcher:
        pid = 111  # The venv launcher is not the OCR worker's parent.
        returncode = None

        def __init__(self, command, **options):
            assert options["stdin"] == module.subprocess.PIPE
            Path(command[-1]).write_text(json.dumps({"pid": 222}))

        def poll(self):
            return self.returncode

        def communicate(self, input=None, timeout=None):
            assert input == b"start\n"
            assert 111 not in processes._OWNED_HELPER_PARENTS
            assert processes._OWNED_HELPER_PARENTS[222] == backend.executable
            snapshot.append({"Id": 333, "Path": str(backend.executable), "parent": 222, "type": "wxocr"})
            assert processes.check_instance(backend.executable, 7)
            snapshot.pop()
            self.returncode = 0
            return json.dumps({"text": "fixture", "lines": ["fixture"]}).encode(), None

    monkeypatch.setattr(module.subprocess, "Popen", Launcher)
    assert backend.extract_image_text(base64.b64encode(png()).decode())["text"] == "fixture"
    assert not processes._OWNED_HELPER_PARENTS


@pytest.mark.parametrize(
    ("handle", "error", "exit_code", "expected"),
    [(77, 0, 0, []), (0, 87, 0, []), (0, 5, 0, [7]), (77, 0, 259, [7])],
)
def test_process_snapshot_omits_only_confirmed_exits(monkeypatch, handle, error, exit_code, expected):
    import ctypes
    from types import SimpleNamespace

    from wechat_cli.backends.windows import processes

    class Function:
        def __init__(self, call):
            self.call = call

        def __call__(self, *args):
            return self.call(*args)

    def first(snapshot, item):
        item._obj.exe, item._obj.pid, item._obj.parent = "Weixin.exe", 7, 1
        return True

    def exit_status(process, status):
        status._obj.value = exit_code
        return True

    kernel = SimpleNamespace(
        CreateToolhelp32Snapshot=Function(lambda *_: 100),
        Process32FirstW=Function(first),
        Process32NextW=Function(lambda *_: False),
        OpenProcess=Function(lambda *_: handle),
        QueryFullProcessImageNameW=Function(lambda *_: False),
        GetExitCodeProcess=Function(exit_status),
        CloseHandle=Function(lambda *_: True),
    )
    native = SimpleNamespace(NtQueryInformationProcess=Function(lambda *_: 0))
    monkeypatch.setattr(
        ctypes, "WinDLL", lambda name, **_: kernel if name == "kernel32" else native, raising=False
    )
    monkeypatch.setattr(ctypes, "get_last_error", lambda: error, raising=False)
    assert [p["Id"] for p in processes.process_snapshot()] == expected
