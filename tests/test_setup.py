import io
import json
import os
import struct
from pathlib import Path

import pytest

from wechat_cli.backends.macos import setup as setup_impl
from wechat_cli.backends.macos.setup import GADGET_LOAD, check_app, download, inject_gadget, load_runtime
from wechat_cli.core.config import api_token, save_json
from wechat_cli.core.errors import AppError


def macho(padding=256):
    # A minimal real 64-bit header and LC_SEGMENT_64, followed by padding and nonzero code.
    command = struct.pack("<II16sQQQQIIII", 0x19, 72, b"__TEXT", 0, 4096, 0, 4096, 5, 5, 0, 0)
    header = struct.pack("<IIIIIIII", 0xFEEDFACF, 0x100000C, 0, 2, 1, len(command), 0, 0)
    return header + command + bytes(padding) + b"CODE" * 10


def test_patch_thin_and_idempotent(tmp_path):
    path = tmp_path / "WeChat"
    original = macho()
    path.write_bytes(original)
    inject_gadget(path)
    first = path.read_bytes()
    assert first.count(GADGET_LOAD) == 1
    assert struct.unpack_from("<I", first, 16)[0] == 2
    assert first[-40:] == original[-40:]
    inject_gadget(path)
    assert path.read_bytes() == first


def test_patch_universal(tmp_path):
    first = macho()
    second = macho()
    offset1, offset2 = 0x1000, 0x2000
    header = struct.pack(">II", 0xCAFEBABE, 2)
    header += struct.pack(">IIIII", 0x1000007, 3, offset1, len(first), 12)
    header += struct.pack(">IIIII", 0x100000C, 0, offset2, len(second), 12)
    data = bytearray(header.ljust(offset2 + len(second), b"\0"))
    data[offset1 : offset1 + len(first)] = first
    data[offset2 : offset2 + len(second)] = second
    path = tmp_path / "WeChat"
    path.write_bytes(data)
    inject_gadget(path)
    assert path.read_bytes().count(GADGET_LOAD) == 2


@pytest.mark.parametrize("data", [b"invalid", macho(padding=0), b"\xca\xfe\xba\xbe" + b"\xff" * 50])
def test_patch_refuses_malformed_or_occupied_padding(tmp_path, data):
    path = tmp_path / "WeChat"
    path.write_bytes(data)
    with pytest.raises((AppError, struct.error)):
        inject_gadget(path)
    assert path.read_bytes() == data


def test_gadget_checks_loopback_and_authentication(tmp_path, monkeypatch):
    monkeypatch.setattr(setup_impl, "check_payload", lambda app: None)
    app = tmp_path / "WeChat.app"
    frameworks = app / "Contents/Frameworks"
    frameworks.mkdir(parents=True)
    (frameworks / "FridaGadget.dylib").write_bytes(b"gadget")
    for interaction in [
        {"type": "listen", "address": "0.0.0.0", "port": 1234, "token": "x" * 32},
        {"type": "listen", "port": True, "token": "x" * 32},
        {"type": "listen", "port": 1234, "token": "short"},
    ]:
        (frameworks / "FridaGadget.config").write_text(json.dumps({"interaction": interaction}))
        with pytest.raises(AppError):
            check_app(app)
    save_json(
        frameworks / "FridaGadget.config",
        {"interaction": {"type": "listen", "port": 1234, "token": "x" * 32}},
    )
    assert check_app(app) == app


def test_download_atomic_checksum_and_size(tmp_path, monkeypatch):
    class Response(io.BytesIO):
        url = "https://example.test/file"

    monkeypatch.setattr(setup_impl.urllib.request, "urlopen", lambda *a, **kw: Response(b"content"))
    path = tmp_path / "download"
    with pytest.raises(AppError) as caught:
        download("https://example.test/file", path, 100, "bad-checksum")
    assert caught.value.code == "download_checksum_mismatch"
    assert not path.exists() and not path.with_suffix(".part").exists()
    with pytest.raises(AppError) as caught:
        download("https://example.test/file", path, 3)
    assert caught.value.code == "download_too_large"
    assert download("https://example.test/file", path, 100).read_bytes() == b"content"
    with pytest.raises(AppError):
        download("http://example.test/file", path, 100)


def test_configuration_is_private_and_token_stable(tmp_path):
    first = api_token(tmp_path)
    assert api_token(tmp_path) == first and len(first) == 64
    if os.name != "nt":
        assert (tmp_path / "api-token").stat().st_mode & 0o777 == 0o600
        assert tmp_path.stat().st_mode & 0o777 == 0o700
    app = tmp_path / "WeChat.app"
    library = app / "Contents/Frameworks/native-reader-test.dylib"
    library.parent.mkdir(parents=True)
    library.write_bytes(b"")
    save_json(tmp_path / "native-runtime.json", {"app": str(app), "supportLibrary": library.name})
    assert load_runtime(tmp_path)["app"] == str(app)
    save_json(tmp_path / "native-runtime.json", {"app": str(app), "supportLibrary": "../../escape.dylib"})
    with pytest.raises(AppError):
        load_runtime(tmp_path)


def test_prepare_never_overwrites_source_or_existing_app(tmp_path):
    source = tmp_path / "Stock.app"
    source.mkdir()
    for destination in [source, source / "copy.app", Path("/Applications/WeChat.app")]:
        with pytest.raises(AppError):
            setup_impl.prepare(source, destination, "gadget", tmp_path)
    destination = tmp_path / "existing.app"
    destination.mkdir()
    with pytest.raises(AppError) as caught:
        setup_impl.prepare(source, destination, "gadget", tmp_path)
    assert caught.value.code == "destination_exists"


def test_inject_never_overwrites_zero_filled_section_data(tmp_path):
    # A file-backed section starts inside otherwise empty-looking header padding.
    segment_size = 72 + 80
    code_offset = 32 + segment_size + 8
    command = struct.pack("<II16sQQQQIIII", 0x19, segment_size, b"__TEXT", 0, 4096, 0, 4096, 5, 5, 1, 0)
    section = struct.pack(
        "<16s16sQQIIIIIIII", b"__text", b"__TEXT", code_offset, 256, code_offset, 2, 0, 0, 0, 0, 0, 0
    )
    header = struct.pack("<IIIIIIII", 0xFEEDFACF, 0x100000C, 0, 2, 1, segment_size, 0, 0)
    original = header + command + section + bytes(512)
    path = tmp_path / "WeChat"
    path.write_bytes(original)
    with pytest.raises(AppError) as caught:
        inject_gadget(path)
    assert caught.value.code == "macho_padding_unavailable"
    assert path.read_bytes() == original


@pytest.mark.skipif(os.name == "nt", reason="macOS copy preserves POSIX directory permissions")
def test_prepare_preserves_existing_destination_parent_permissions(tmp_path, monkeypatch):
    parent = tmp_path / "Applications"
    parent.mkdir(mode=0o755)
    source = tmp_path / "Stock.app"
    source.mkdir()
    monkeypatch.setattr(setup_impl, "check_payload", lambda app: None)

    def fail_copy(*args, **kwargs):
        assert parent.stat().st_mode & 0o777 == 0o755
        raise AppError("fixture_stop", "stop before copy")

    monkeypatch.setattr(setup_impl, "execute", fail_copy)
    with pytest.raises(AppError) as caught:
        setup_impl.prepare(source, parent / "Bridge.app", "gadget", tmp_path)
    assert caught.value.code == "fixture_stop"
    assert parent.stat().st_mode & 0o777 == 0o755
