import json
import subprocess
import sys

import pytest

from wechat_cli.core.config import save_json
from wechat_cli.core.errors import AppError
from wechat_cli.core.models import receipt
from wechat_cli.runtime.transport import WorkerTransport

WORKER = """import json,sys,time
print(json.dumps({'event':'ready','protocol':1,'pid':123}),flush=True)
for line in sys.stdin:
 r=json.loads(line)
 if r['method']=='hang': time.sleep(5)
 elif r['method']=='die': sys.exit(1)
 else: print(json.dumps({'id':r['id'],'result':{'connected':True}}),flush=True)
"""


def bridge(tmp_path, worker=WORKER):
    app = tmp_path / "App.app"
    library = app / "Contents/Frameworks/native-reader-test.dylib"
    library.parent.mkdir(parents=True, exist_ok=True)
    library.write_bytes(b"")
    save_json(tmp_path / "native-runtime.json", {"app": str(app), "supportLibrary": library.name})

    def spawn(args, **kwargs):
        return subprocess.Popen([sys.executable, "-u", "-c", worker], **kwargs)

    return WorkerTransport([sys.executable], dict(__import__("os").environ), timeout=0.2, popen=spawn)


def test_actual_worker_transport_and_cleanup(tmp_path):
    native = bridge(tmp_path)
    assert native.request("status")["connected"] is True
    assert native.request("status")["connected"] is True
    child = native.process
    native.close()
    assert child.poll() == 0
    with pytest.raises(AppError):
        native.connect()


def test_native_timeout_and_disconnection(tmp_path):
    native = bridge(tmp_path)
    with pytest.raises(AppError) as caught:
        native.request("hang")
    assert caught.value.code == "native_read_timeout"
    assert not native.pending
    native.process.kill()
    native.process.wait()
    native.close()
    native = bridge(tmp_path)
    with pytest.raises(AppError) as caught:
        native.request("die")
    assert caught.value.code == "native_bridge_unavailable"
    native.close()


def test_startup_failure_is_actionable(tmp_path):
    native = bridge(
        tmp_path, "import json;print(json.dumps({'event':'failure','error':'stock_app_running'}),flush=True)"
    )
    with pytest.raises(AppError) as caught:
        native.connect()
    assert caught.value.code == "stock_app_running"
    native.close()


def test_gui_event_prevents_requests_and_never_restarts_worker(tmp_path):
    worker = WORKER.replace(
        "if r['method']=='hang': time.sleep(5)",
        "if r['method']=='closeGui': print(json.dumps({'event':'stopped','reason':'gui_closed'}),flush=True); print(json.dumps({'id':r['id'],'result':{}}),flush=True)",
    )
    native = bridge(tmp_path, worker)
    native.request("closeGui")
    assert native.stopped.wait(timeout=1)
    child = native.process
    with pytest.raises(AppError):
        native.request("status")
    assert native.process is child
    assert native.stop_reason == "gui_closed"
    native.close()


def test_worker_death_cannot_relaunch_gui(tmp_path):
    native = bridge(tmp_path)
    with pytest.raises(AppError):
        native.request("die")
    assert native.stopped.wait(timeout=1)
    with pytest.raises(AppError):
        native.request("status")
    native.close()


def test_receipt_does_not_claim_delivery():
    result = receipt({"source": "native", "queued": True, "receiverVerified": True, "requestId": "x"}, "chat")
    assert result["acceptedByClient"] is True and result["acknowledgedByServer"] is False
    with pytest.raises(AppError) as caught:
        receipt({"queued": True}, "chat")
    assert caught.value.code == "native_send_outcome_unknown"


def test_worker_resumes_app_left_waiting_by_earlier_startup(tmp_path, monkeypatch):
    import hashlib
    import io
    import runpy
    import types

    from wechat_cli.backends.macos import setup as setup_impl

    app = tmp_path / "Waiting.app"
    (app / "Contents/Frameworks").mkdir(parents=True)
    (app / "Contents/Resources").mkdir()
    (app / "Contents/Resources/wechat.dylib").write_bytes(b"fixture payload")
    (app / "Contents/Frameworks/FridaGadget.config").write_text(
        json.dumps({"interaction": {"port": 12345, "token": "test-token-123456789"}})
    )
    monkeypatch.setenv("WECHAT_NATIVE_APP", str(app))
    monkeypatch.setenv("WECHAT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("WECHAT_NATIVE_SUPPORT", "native-reader-fixture.dylib")
    monkeypatch.setattr(
        setup_impl,
        "check_payload",
        lambda app: {"payloadSha256": hashlib.sha256(b"fixture payload").hexdigest()},
    )
    from wechat_cli.backends.macos import processes

    monkeypatch.setattr(processes, "check_instance", lambda *args: 123)
    calls = []
    exports = types.SimpleNamespace(
        startup_status=lambda: {
            "pid": 123,
            "executable": str(app / "Contents/MacOS/WeChat"),
            "applicationRunning": False,
        }
    )
    script = types.SimpleNamespace(
        load=lambda: None, unload=lambda: None, exports_sync=exports, on=lambda *args: None
    )
    session = types.SimpleNamespace(
        create_script=lambda source: script, detach=lambda: None, on=lambda *args: None
    )
    device = types.SimpleNamespace(attach=lambda name: session, resume=lambda pid: calls.append(pid))
    manager = types.SimpleNamespace(
        add_remote_device=lambda *args, **kwargs: device, remove_remote_device=lambda endpoint: None
    )
    monkeypatch.setitem(
        sys.modules,
        "frida",
        types.SimpleNamespace(get_device_manager=lambda: manager, InvalidOperationError=RuntimeError),
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    runpy.run_module("wechat_cli.backends.macos.worker", run_name="__main__")
    assert calls == [123]
