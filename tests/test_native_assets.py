"""Release assets are mandatory; setup and controls must never compile at runtime."""

import hashlib
import json
import platform
from pathlib import Path
from types import SimpleNamespace

import pytest

from wechat_cli.backends.macos import controls, native_assets, setup
from wechat_cli.core.errors import AppError


def fixture_asset(tmp_path, monkeypatch, name, data=b"prebuilt helper"):
    binaries = tmp_path / "bin"
    binaries.mkdir(exist_ok=True)
    (binaries / name).write_bytes(data)
    (binaries / "manifest.json").write_text(
        json.dumps({"files": {name: {"sha256": hashlib.sha256(data).hexdigest()}}})
    )
    monkeypatch.setattr(native_assets, "BINARIES", binaries)
    return binaries / name


def test_missing_or_corrupted_asset_never_falls_back_to_compiler(tmp_path, monkeypatch):
    path = fixture_asset(tmp_path, monkeypatch, "native-reader.dylib")
    assert native_assets.native_asset(path.name) == path
    path.write_bytes(b"damaged")
    with pytest.raises(AppError, match="Reinstall wechat-cli") as error:
        native_assets.native_asset(path.name)
    assert error.value.code == "native_asset_missing"
    path.unlink()
    with pytest.raises(AppError):
        native_assets.native_asset(path.name)


def test_setup_copies_and_repairs_prebuilt_library(tmp_path, monkeypatch):
    source = fixture_asset(tmp_path, monkeypatch, "native-reader.dylib")
    app = tmp_path / "Test.app"
    frameworks = app / "Contents/Frameworks"
    frameworks.mkdir(parents=True)
    commands = []
    monkeypatch.setattr(setup, "execute", lambda args: commands.append(args))
    name = setup.build_helper(app)
    library = frameworks / name
    assert library.read_bytes() == source.read_bytes()
    library.write_bytes(b"corrupt cached copy")
    assert setup.build_helper(app) == name
    assert library.read_bytes() == source.read_bytes()
    assert all(command[0] == "/usr/bin/codesign" for command in commands)


def test_controls_run_prebuilt_executable(tmp_path, monkeypatch):
    binary = fixture_asset(tmp_path, monkeypatch, "native-controls")
    monkeypatch.setattr(controls, "load_runtime", lambda directory: {"app": str(tmp_path / "Test.app")})
    monkeypatch.setattr(controls, "check_instance", lambda expected: 123)

    def run(args, **kwargs):
        assert args == [str(binary)]
        assert json.loads(kwargs["input"])["pid"] == 123
        return SimpleNamespace(stdout='{"ok": true}')

    monkeypatch.setattr(controls.subprocess, "run", run)
    assert controls.Controls(tmp_path).invoke("status") == {"ok": True}


def test_release_sources_match_manifest():
    assets = Path(native_assets.__file__).parent / "assets"
    manifest = json.loads((assets / "bin/manifest.json").read_text())
    for name, digest in manifest["sources"].items():
        assert hashlib.sha256((assets / name).read_bytes()).hexdigest() == digest
    for name in manifest["files"]:
        assert native_assets.native_asset(name).is_file()


@pytest.mark.skipif(
    platform.system() != "Darwin" or platform.machine() != "arm64", reason="Apple Silicon runtime smoke test"
)
def test_signed_helpers_work_without_xcode_discovery(monkeypatch):
    import ctypes
    import subprocess

    monkeypatch.setenv("DEVELOPER_DIR", "/nonexistent")
    monkeypatch.setenv("SDKROOT", "/nonexistent")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    for name in ["native-reader.dylib", "channels-native-v2.dylib", "silk.dylib"]:
        ctypes.CDLL(str(native_assets.native_asset(name)))
    # Validation returns before any Accessibility check or UI operation.
    result = subprocess.run(
        [str(native_assets.native_asset("native-controls"))],
        input='{"action":"moments.inspect"}',
        text=True,
        capture_output=True,
        check=True,
        timeout=10,
    )
    assert json.loads(result.stdout)["error"] == "invalid_input"
