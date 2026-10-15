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

The original app bundle was not modified. The live checks used a separately copied app with the current helper. Real text/file/voice sends and cloud voice recognition were not invoked. Windows/Linux native backends remain unsupported; shared structure and host primitives do not imply validated native support.

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
