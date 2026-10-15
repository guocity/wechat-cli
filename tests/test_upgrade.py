import hashlib
import json
import shutil
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest

from wechat_cli.backends.macos import backend, setup, upgrade
from wechat_cli.core.config import save_json
from wechat_cli.core.errors import AppError

LIBRARY = "native-reader-fixture.dylib"


def app_copy(path, payload):
    frameworks = path / "Contents/Frameworks"
    frameworks.mkdir(parents=True)
    (frameworks / LIBRARY).write_bytes(b"helper")
    (frameworks / "FridaGadget.dylib").write_bytes(b"gadget")
    save_json(
        frameworks / "FridaGadget.config",
        {"interaction": {"type": "listen", "port": 12345, "token": "test-token-123456789"}},
    )
    resources = path / "Contents/Resources"
    resources.mkdir()
    (resources / "wechat.dylib").write_bytes(payload)
    return {"app": str(path), "supportLibrary": LIBRARY}


@pytest.fixture
def release(tmp_path, monkeypatch):
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    old = {"version": "4.1.13.34", "build": "269602", "payloadSha256": hashlib.sha256(b"old").hexdigest()}
    latest = {"version": "4.1.15", "build": "270135", "payloadSha256": hashlib.sha256(b"latest").hexdigest()}
    save_json(profiles / "old.json", old)
    save_json(profiles / "latest.json", latest)
    monkeypatch.setattr(setup, "PROFILES", profiles)
    monkeypatch.setattr(upgrade, "LATEST_PROFILE", profiles / "latest.json")
    monkeypatch.setattr(setup, "require_macos", lambda: None)
    monkeypatch.setattr(setup, "build_helper", lambda app: LIBRARY)
    monkeypatch.setattr(upgrade, "check_instance", lambda *args: None)
    directory = tmp_path / "state"
    original = app_copy(tmp_path / "ExternalOld.app", b"old")
    save_json(directory / "native-runtime.json", original)
    return SimpleNamespace(directory=directory, original=original, latest=latest, profiles=profiles)


def stage_release(release, payload=b"latest"):
    def stage(directory, **kwargs):
        assert directory == release.directory
        assert kwargs["publish"] is False
        assert kwargs["expected_payload"] == release.latest["payloadSha256"]
        assert setup.load_runtime(directory) == release.original
        return app_copy(kwargs["destination"], payload)

    return stage


def test_startup_upgrades_old_bridge_and_preserves_original(release, monkeypatch, capsys):
    monkeypatch.setattr(setup, "setup", stage_release(release))
    updated = upgrade.ensure_current(release.directory)
    assert Path(updated["app"]).parent == release.directory / "apps"
    assert "4.1.15-270135" in updated["app"]
    assert setup.load_runtime(release.directory) == updated
    assert json.loads((release.directory / "previous-native-runtime.json").read_text()) == release.original
    assert (Path(release.original["app"]) / "Contents/Resources/wechat.dylib").read_bytes() == b"old"
    output = capsys.readouterr()
    assert not output.out and '"to": "4.1.15"' in output.err
    monkeypatch.setattr(setup, "setup", lambda *a, **kw: pytest.fail("Already current: no download/build"))
    assert upgrade.ensure_current(release.directory) == updated


def test_verified_cached_latest_copy_is_reused_without_download(release, monkeypatch):
    destination = release.directory / "apps/WeChatBridge-4.1.15-270135.app"
    app_copy(destination, b"latest")
    monkeypatch.setattr(setup, "download", lambda *a, **kw: pytest.fail("Cached app must not download"))
    assert upgrade.ensure_current(release.directory)["app"] == str(destination)


def test_existing_default_latest_copy_is_reused(release, monkeypatch):
    destination = release.directory / "apps/WeChatBridge.app"
    app_copy(destination, b"latest")
    monkeypatch.setattr(setup, "download", lambda *a, **kw: pytest.fail("Stored latest app must be reused"))
    assert upgrade.ensure_current(release.directory)["app"] == str(destination)


def test_preparation_failure_preserves_config_and_retry_succeeds(release, monkeypatch):
    before = (release.directory / "native-runtime.json").read_bytes()

    def fail(*args, **kwargs):
        raise AppError("download_failed", "Network unavailable", 424)

    monkeypatch.setattr(setup, "setup", fail)
    with pytest.raises(AppError, match="Network unavailable"):
        upgrade.ensure_current(release.directory)
    assert (release.directory / "native-runtime.json").read_bytes() == before
    assert not (release.directory / "previous-native-runtime.json").exists()
    monkeypatch.setattr(setup, "setup", stage_release(release))
    assert "4.1.15" in upgrade.ensure_current(release.directory)["app"]


def test_wrong_downloaded_release_cannot_replace_old_config(release, monkeypatch):
    monkeypatch.setattr(setup, "setup", stage_release(release, b"old"))
    with pytest.raises(AppError) as caught:
        upgrade.ensure_current(release.directory)
    assert caught.value.code == "unexpected_wechat_payload"
    assert setup.load_runtime(release.directory) == release.original


def test_expected_release_is_checked_before_copying(release, monkeypatch):
    monkeypatch.setattr(setup, "execute", lambda *a, **kw: pytest.fail("Must reject before copy/sign"))
    with pytest.raises(AppError) as caught:
        setup.prepare(
            Path(release.original["app"]),
            release.directory / "wrong.app",
            "gadget",
            release.directory,
            publish=False,
            expected_payload=release.latest["payloadSha256"],
        )
    assert caught.value.code == "unexpected_wechat_payload"
    assert setup.load_runtime(release.directory) == release.original


def test_preparing_copy_does_not_publish_runtime_before_activation(release, monkeypatch):
    source = Path(release.original["app"])
    executable = source / "Contents/MacOS/WeChat"
    executable.parent.mkdir()
    executable.write_bytes(struct.pack("<IIIIIIII", 0xFEEDFACF, 0x100000C, 0, 2, 0, 0, 0, 0) + bytes(512))

    def execute(args, **kwargs):
        if args[0] == "/usr/bin/ditto":
            shutil.copytree(args[1], args[2])
        return b""

    monkeypatch.setattr(setup, "execute", execute)
    destination = release.directory / "staged.app"
    prepared = setup.prepare(
        source,
        destination,
        source / "Contents/Frameworks/FridaGadget.dylib",
        release.directory,
        publish=False,
    )
    assert prepared["supportLibrary"] == LIBRARY
    assert setup.GADGET_LOAD in (destination / "Contents/MacOS/WeChat").read_bytes()
    assert setup.load_runtime(release.directory) == release.original


def test_running_old_bridge_requires_exit_before_upgrade(release, monkeypatch):
    monkeypatch.setattr(upgrade, "check_instance", lambda *a: 123)
    monkeypatch.setattr(setup, "setup", lambda *a, **kw: pytest.fail("Do not modify a running app"))
    with pytest.raises(AppError) as caught:
        upgrade.ensure_current(release.directory)
    assert caught.value.code == "wechat_update_requires_exit"
    assert "Quit" in str(caught.value) and "Only one WeChat" in str(caught.value)
    assert setup.load_runtime(release.directory) == release.original


def test_app_started_during_download_prevents_activation(release, monkeypatch):
    checks = iter([None, 123])
    monkeypatch.setattr(upgrade, "check_instance", lambda *a: next(checks))
    monkeypatch.setattr(setup, "setup", stage_release(release))
    with pytest.raises(AppError) as caught:
        upgrade.ensure_current(release.directory)
    assert caught.value.code == "wechat_update_requires_exit"
    assert setup.load_runtime(release.directory) == release.original


def test_newer_validated_copy_is_never_downgraded(release, monkeypatch):
    profile = {"version": "4.1.16", "build": "280000", "payloadSha256": hashlib.sha256(b"newer").hexdigest()}
    save_json(release.profiles / "newer.json", profile)
    current = app_copy(release.directory / "Newer.app", b"newer")
    save_json(release.directory / "native-runtime.json", current)
    monkeypatch.setattr(setup, "setup", lambda *a, **kw: pytest.fail("Never downgrade"))
    assert upgrade.ensure_current(release.directory) == current


def test_failed_atomic_activation_preserves_old_pointer(release, monkeypatch):
    monkeypatch.setattr(setup, "setup", stage_release(release))

    def fail_activation(path, value):
        if path.name == "native-runtime.json":
            raise OSError("cannot replace pointer")
        save_json(path, value)

    monkeypatch.setattr(upgrade, "save_json", fail_activation)
    with pytest.raises(AppError) as caught:
        upgrade.ensure_current(release.directory)
    assert caught.value.code == "wechat_upgrade_failed"
    assert setup.load_runtime(release.directory) == release.original


def test_symlink_upgrade_destination_is_never_followed(release):
    destination = release.directory / "apps/WeChatBridge-4.1.15-270135.app"
    destination.parent.mkdir()
    destination.symlink_to(release.original["app"], target_is_directory=True)
    with pytest.raises(AppError) as caught:
        upgrade.ensure_current(release.directory)
    assert caught.value.code == "invalid_destination"


def test_backend_launches_selected_upgrade_instead_of_old_app(release, monkeypatch):
    replacement = app_copy(release.directory / "New.app", b"latest")
    calls = []
    monkeypatch.setattr(backend, "ensure_current", lambda directory: replacement)
    monkeypatch.setattr(backend, "check_instance", lambda *a: 123)

    def transport(command, environment):
        calls.append(environment)
        return SimpleNamespace(connect=lambda: None, pid=123)

    monkeypatch.setattr(backend, "WorkerTransport", transport)
    backend.MacOSBackend(release.directory).connect()
    assert calls[0]["WECHAT_NATIVE_APP"] == replacement["app"]


def test_upgrade_finishes_before_background_startup_timeout(tmp_path, monkeypatch):
    from wechat_cli.runtime import lifecycle

    staged = []
    backend_module = SimpleNamespace(ensure_current=lambda directory: staged.append(directory))
    monkeypatch.setattr("wechat_cli.backends.registry.platform_backend", lambda: backend_module)
    monkeypatch.setattr(lifecycle, "default_data_directory", lambda: tmp_path / "global")

    def health(*args):
        if not staged:
            raise AppError("api_unavailable", "not started")
        return {"service": "wechat-cli"}

    def spawn(*args, **kwargs):
        assert staged == [tmp_path]
        return SimpleNamespace(poll=lambda: None)

    monkeypatch.setattr(lifecycle, "health", health)
    monkeypatch.setattr(lifecycle.subprocess, "Popen", spawn)
    assert lifecycle.ensure_server(tmp_path, 8787) == {"service": "wechat-cli"}
