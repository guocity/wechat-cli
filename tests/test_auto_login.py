from wechat_cli.backends.macos.backend import MacOSBackend


def test_signed_in_account_does_not_touch_login_ui(tmp_path):
    backend = MacOSBackend(tmp_path)
    backend.status = lambda: {"accountAvailable": True}
    backend.controls.invoke = lambda action: (_ for _ in ()).throw(AssertionError(action))
    backend.auto_login()


def test_remembered_account_waits_for_native_login(tmp_path, monkeypatch):
    backend = MacOSBackend(tmp_path)
    states = iter([False, False, True])
    backend.status = lambda: {"accountAvailable": next(states)}
    calls = []
    backend.controls.invoke = lambda action: calls.append(action) or {"loginStarted": True}
    monkeypatch.setattr("wechat_cli.backends.macos.backend.time.sleep", lambda _: None)
    backend.auto_login()
    assert calls == ["login.saved"]


def test_no_saved_account_leaves_manual_login_available(tmp_path):
    backend = MacOSBackend(tmp_path)
    backend.status = lambda: {"accountAvailable": False}
    backend.controls.invoke = lambda action: {"loginStarted": False}
    backend.auto_login()
