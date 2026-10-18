from wechat_cli.backends.macos.backend import MacOSBackend


def native_backend(tmp_path):
    backend = MacOSBackend(tmp_path)
    backend.controls.invoke = lambda action: (_ for _ in ()).throw(AssertionError(action))
    return backend


def test_signed_in_account_does_not_start_login(tmp_path):
    backend = native_backend(tmp_path)
    backend.status = lambda: {"accountAvailable": True}
    backend._request = lambda action: (_ for _ in ()).throw(AssertionError(action))
    backend.auto_login()


def test_remembered_account_waits_for_native_login(tmp_path, monkeypatch):
    backend = native_backend(tmp_path)
    states = iter([False, False, True])
    backend.status = lambda: {"accountAvailable": next(states)}
    calls = []
    backend._request = lambda action: calls.append(action) or {"supported": True}
    monkeypatch.setattr("wechat_cli.backends.macos.backend.time.sleep", lambda _: None)
    backend.auto_login()
    assert calls == ["loginSaved"]


def test_no_saved_account_leaves_manual_login_available(tmp_path):
    backend = native_backend(tmp_path)
    backend.status = lambda: {"accountAvailable": False}
    backend._request = lambda action: {"supported": True, "savedAccountAvailable": False}
    backend.auto_login()


def test_saved_account_callback_without_credentials_stops_waiting(tmp_path):
    backend = native_backend(tmp_path)
    backend.status = lambda: {
        "accountAvailable": False,
        "autoLogin": {"savedAccountAvailable": False},
    }
    backend._request = lambda action: {"supported": True}
    backend.auto_login()


def test_unverified_native_version_does_not_attempt_login(tmp_path):
    backend = native_backend(tmp_path)
    backend.status = lambda: {"accountAvailable": False}
    backend._request = lambda action: {"supported": False}
    backend.auto_login()


def test_cold_start_waits_for_native_image_before_requesting_saved_login(tmp_path, monkeypatch):
    backend = native_backend(tmp_path)
    states = iter(
        [
            {"state": "waiting_for_module", "accountAvailable": False},
            {"state": "waiting_for_live_image", "accountAvailable": False},
            {"state": "native_ready", "accountAvailable": False},
            {"state": "native_ready", "accountAvailable": True},
        ]
    )
    backend.status = lambda: next(states)
    calls = []
    backend._request = lambda action: calls.append(action) or {"supported": True}
    monkeypatch.setattr("wechat_cli.backends.macos.backend.time.sleep", lambda _: None)
    backend.auto_login()
    assert calls == ["loginSaved"]
