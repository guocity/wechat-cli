import subprocess
import sys

import pytest
from conftest import FakeNative, running_server

from wechat_cli.api import app as app_module
from wechat_cli.backends.macos.processes import check_instance, main_processes
from wechat_cli.backends.registry import platform_backend
from wechat_cli.cli import main
from wechat_cli.core.errors import AppError
from wechat_cli.host import instance_error


@pytest.mark.parametrize("reason", ["gui_closed", "wechat_closed", "bridge_disconnected"])
def test_gui_or_bridge_exit_stops_uvicorn_and_closes_resources(tmp_path, reason):
    with running_server(tmp_path) as (server, client, native):
        assert client.chats()["total"] == 1
        native.stop_reason = reason
        native.alive = False
        server.thread.join(timeout=3)
        assert not server.thread.is_alive()
        assert native.closed
        assert server.app.state.shutdown_reason == reason
        with pytest.raises(AppError) as caught:
            client.chats()
        assert caught.value.code == "api_unavailable"
    # A fresh session can start after teardown; the dead worker was never restarted.
    with running_server(tmp_path) as (_, client, _):
        assert client.chats()["total"] == 1


def test_second_instance_across_data_directories_is_rejected_and_lock_released(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "default_data_directory", lambda: tmp_path / "user-state")
    with running_server(tmp_path / "first", singleton=True):
        second = FakeNative()
        second.connect = lambda: pytest.fail("Second backend must not connect")
        with pytest.raises(AppError) as caught:
            with running_server(tmp_path / "second", native=second, singleton=True):
                pass
        assert caught.value.code == "wechat_instance_conflict"
        assert "Only one WeChat instance" in str(caught.value)
    with running_server(tmp_path / "second", singleton=True) as (_, client, _):
        assert client.chats()["total"] == 1


def test_startup_failure_cleans_backend_and_releases_instance_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "default_data_directory", lambda: tmp_path / "user-state")
    native = FakeNative()

    def fail():
        raise instance_error()

    native.connect = fail
    with pytest.raises(AppError):
        with running_server(tmp_path / "broken", native=native, singleton=True):
            pass
    assert native.closed
    with running_server(tmp_path / "recovered", singleton=True):
        pass


def test_process_guard_counts_stock_and_copied_apps_but_excludes_helpers(tmp_path):
    expected = tmp_path / "Bridge.app/Contents/MacOS/WeChat"
    own = f"123 {expected}"
    helper = "124 /Applications/WeChat.app/Contents/MacOS/WeChatHelper"
    assert list(main_processes(own + "\n" + helper)) == [123]
    assert check_instance(expected, snapshot=own + "\n" + helper) == 123
    assert check_instance(expected, 123, snapshot=own)
    assert not check_instance(expected, 456, snapshot=own)
    for other in [
        "456 /Applications/WeChat.app/Contents/MacOS/WeChat",
        "456 /Volumes/Installer/微信.app/Contents/MacOS/Weixin",
        f"456 {expected}",
    ]:
        with pytest.raises(AppError) as caught:
            check_instance(expected, snapshot=own + "\n" + other)
        assert caught.value.code == "wechat_instance_conflict"
    assert check_instance(expected, snapshot="") is None


def test_conflict_after_startup_stops_server(tmp_path):
    with running_server(tmp_path) as (server, _, native):
        native.is_alive = lambda: (_ for _ in ()).throw(instance_error())
        server.thread.join(timeout=3)
        assert server.app.state.shutdown_reason == "wechat_instance_conflict"
        assert native.closed


def test_cli_explains_single_instance_rule(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("wechat_cli.cli.ensure_server", lambda *args: (_ for _ in ()).throw(instance_error()))
    assert main(["start", "--data-dir", str(tmp_path)]) == 1
    assert "Only one WeChat instance is allowed" in capsys.readouterr().err


def test_unsupported_host_does_not_load_macos_backend(monkeypatch):
    monkeypatch.setattr("platform.system", lambda: "Linux")
    with pytest.raises(AppError) as caught:
        platform_backend()
    assert caught.value.code == "unsupported_platform"


def test_cli_import_does_not_load_platform_backend():
    script = "import wechat_cli.cli,sys; assert not any(m.startswith('wechat_cli.backends.macos') for m in sys.modules); assert 'PIL' not in sys.modules; assert 'frida' not in sys.modules"
    subprocess.run([sys.executable, "-c", script], check=True, timeout=10)


def test_authenticated_stop_closes_bridge_and_port(api):
    server, client, native = api
    assert client.request("/api/server/stop", method="POST", data={}) == {"stopping": True}
    server.thread.join(timeout=3)
    assert native.closed
    assert not server.thread.is_alive()
