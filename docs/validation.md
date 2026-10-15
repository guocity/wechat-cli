# FastAPI/backend refactor validation

Performed on Apple Silicon macOS, October 15, 2026. Python 3.11 development environment; the built wheel was also installed with `uvx` outside the checkout.

- 85 unit/integration tests: actual loopback Uvicorn requests, token/cookie authentication, CSRF/Host checks, bounded bodies, strict inputs, generated schemas, durable/concurrent idempotency, transport timeouts/disconnection, account-scoped receipts, media/codecs, and Mach-O setup guards.
- Lifecycle tests: GUI-close and process/bridge-loss events stop Uvicorn, close the backend/ledger, release the port, and allow a fresh session. A single global lock rejects another server even with a different data directory. Failed startup releases resources. A disconnected transport cannot relaunch its worker or GUI.
- Native process identity: recognizes stock and copied WeChat main processes, excludes helper processes, resolves path aliases, rejects multiple instances, and checks the captured PID rather than attaching to a replacement process.
- Live isolated WeChat 4.1.13.34: 388 conversations, 18 File Transfer messages, string IDs, cached image/voice/file reads, local OCR and text preparation passed through FastAPI. No message was queued or sent.
- Real WeChat application exit stopped the service, made its API unavailable, and left the web page with zero enabled controls and a session-closed message. The user also closed the isolated app with its actual red close button: the server shut down, the API port became unavailable, and no WeChat main process remained. A second-instance conflict was observed during test cleanup; the CLI printed the single-instance instruction. A clean restart succeeded after the owned extra processes were removed.
- Real AppKit/Frida fixtures: normal close, a subclass with deferred Qt-style close, close followed by immediate reopen, and orderOut without process termination all report GUI closure. Minimize, application hide, auxiliary-window close, and login-window transition preserve the session. Callback queues are drained before script unloading. These isolated fixtures additionally establish closure detection when the app process remains running.
- Browser: authenticated, rendered 18 real message cards, kept empty sending disabled, and detected service shutdown without automatically resending or reconnecting.
- Release: Ruff, whitespace checks, wheel/sdist build, Twine metadata checks, package asset/private-state checks and outside-checkout `uvx` execution passed.

The original app bundle was not modified. The live checks used a separately copied app with the current helper. Real text/file/voice sends and cloud voice recognition were not invoked. This section records the original macOS refactor. The subsequent native Windows implementation and live checks are documented in [Windows validation](windows-validation.md). Linux remains unsupported.

## Startup upgrade regression coverage

The suite additionally checks automatic selection from 4.1.13 to the latest validated 4.1.15 release, reuse of both versioned and legacy managed caches, current/newer releases avoiding download or downgrade, exact-payload validation before copying, unpublished staging, preserved configuration after preparation or atomic-activation failures, running-app conflicts before and after a download, symlink rejection, the backend launching the newly selected app, and foreground preparation before the background startup timeout. The release manifest is included in the wheel asset checks.

The full suite now contains **99 passing tests**. A live isolated startup began with a saved 4.1.13.34 / build 269602 reference, downloaded and verified the official 4.1.15 installer, prepared a separate versioned app, and reached `native_ready` on **4.1.15 / build 270135**. The previous runtime pointer and original 4.1.13 app remained intact. No messages were sent and the separately installed app was not modified.

The live registration was then reset to 4.1.13 while retaining the prepared 4.1.15 managed copy. Startup reused that copy, again launched 4.1.15, and left the cached installer's modification timestamp unchanged.

## Reproduce

```sh
uv sync --group dev
uv run pytest
uv run ruff check src tests tools
uv run ruff format --check src tests tools
uv build --no-sources
uv run twine check dist/*
uv run python tools/check_wheel.py dist/wechat_cli-0.1.0-py3-none-any.whl
```

Opt-in native window checks (do not open or control the user's WeChat):

```sh
uv run python tools/test_gui_lifecycle.py --gadget /path/to/FridaGadget.dylib
uv run python tools/test_startup_notice.py --gadget /path/to/FridaGadget.dylib
```

Read-only live API check, after setup/start and normal WeChat login:

```sh
uv run python tools/live_smoke.py --data-dir /path/to/state --port 8787
```
