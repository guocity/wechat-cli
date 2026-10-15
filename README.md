# wechat-cli

A Python CLI, authenticated local REST API, Python client and web interface for a signed-in macOS WeChat session. Node.js and npm are not required. `uvx` installs Python dependencies, including Frida and the SILK codec, into its own environment.

## Quick start

After publication to PyPI:

```sh
uvx wechat-cli setup
uvx wechat-cli start
uvx wechat-cli chats
uvx wechat-cli web
```

`setup` downloads WeChat 4.1.15 from Tencent and Frida Gadget 17.8.0 from the upstream Frida release. It verifies the Gadget archive's SHA-256, the downloaded app's Tencent signature and its supported payload fingerprint, mounts the installer read-only, and prepares a separate app in:

```text
~/Library/Application Support/wechat-cli/apps/WeChatBridge.app
```

It adds Gadget, builds the bundled C++ helper, signs the copy, and records its location. `/Applications/WeChat.app` is not modified. No WeChat app, Gadget binary, credentials or account data are redistributed in the Python package.

Requires **Apple Silicon macOS**, **Python 3.11+** (managed by uv when necessary), **uv**, and **Xcode command-line tools** (`xcode-select --install`). The native helper and SILK codec require a compiler. Setup runs as your normal user; do not use `sudo uvx`.

On CLI startup, the bridge automatically accepts WeChat's exact **“opened from the installation package”** location notice with its **“Do not show this message again”** box selected. This policy is installed before the app resumes; setup itself does not need Accessibility permission to configure it.

Quit other WeChat copies before starting the prepared app. Sign in through its normal WeChat window if needed. `start` launches that app and a persistent FastAPI/Uvicorn service. Later commands call the service and reuse its session; they do not log in again. `web` also starts the service if needed, then opens its local page. `stop` stops the Python service and leaves WeChat running and signed in.

## Supported payloads

| WeChat version | Build | Installer |
| --- | --- | --- |
| 4.1.13.34 | 269602 | Supply an existing compatible app with `--source` or `--app` |
| 4.1.13 | 269631 | Tencent's current `WeChatMac_4.1.13.dmg` |
| 4.1.15 | 270135 | Tencent's current `WeChatMac_4.1.15.dmg` (default) |

Profiles are selected by the entire vendor payload's SHA-256, with native function signatures checked before use. Installer filenames alone do not establish compatibility. Tencent can replace a download without changing its filename. Future payloads require a new validated profile and package release; unknown payloads are rejected before patching. To get the newest package, use `uvx --refresh wechat-cli setup`.

To prepare the current 4.1.13 download:

```sh
uvx wechat-cli setup \
  --wechat-url https://dldir1v6.qq.com/weixin/Universal/Mac/WeChatMac_4.1.13.dmg \
  --destination "$HOME/Applications/WeChatBridge-4.1.13.app"
```

To use a local installer app or an already patched copy:

```sh
uvx wechat-cli setup --source /Applications/WeChat.app
uvx wechat-cli setup --app /absolute/path/to/WeChatBridge.app
uvx wechat-cli doctor
```

`--source` still requires a supported payload. `--gadget /path/to/FridaGadget.dylib` uses a locally supplied Gadget instead of downloading it. `--destination` must be a new path outside the source app. Setup never overwrites an existing app; `--app` registers and rebuilds an existing patched copy. Repeated default setup reuses the previously prepared copy.

## Commands

All results are JSON on stdout. Errors and generated idempotency keys go to stderr. Exit status is 0 for success, 1 for operation errors, and 2 for invalid CLI syntax. Global `--data-dir` and `--port` options work before or after a subcommand.

```sh
uvx wechat-cli start                  # background service + patched app
uvx wechat-cli start --foreground     # foreground service
uvx wechat-cli serve                  # same foreground behavior
uvx wechat-cli status
uvx wechat-cli chats
uvx wechat-cli messages filehelper --limit 20
uvx wechat-cli send filehelper 'Hello' --key demo-message-001
uvx wechat-cli send filehelper 'Validate only' --dry-run
uvx wechat-cli send-file filehelper ./report.pdf --key demo-file-001
uvx wechat-cli send-voice filehelper ./voice.wav --key demo-voice-001
uvx wechat-cli send-voice filehelper ./voice.silk --format silk
uvx wechat-cli send-voice filehelper ./voice.pcm --format pcm_s16le --sample-rate 24000
uvx wechat-cli media CHAT_ID MESSAGE_ID --output ./attachment.bin
uvx wechat-cli download CHAT_ID MESSAGE_ID  # download through the active native chat
uvx wechat-cli ocr CHAT_ID MESSAGE_ID
uvx wechat-cli transcribe CHAT_ID MESSAGE_ID --key demo-transcription-001
uvx wechat-cli transcript CHAT_ID MESSAGE_ID
uvx wechat-cli web
uvx wechat-cli web --no-open
uvx wechat-cli stop
```

Use the exact **string** message IDs returned by `messages`; some exceed JavaScript's precise integer range. History is limited to the latest 100 records per request. `media --output` refuses to overwrite an existing file. Voice input supports SILK, mono 16-bit PCM WAV and raw signed little-endian 16-bit PCM, lasting 1–60 seconds. CLI file/audio uploads are limited to 8 MiB. File sending uses WeChat's UI and requires Accessibility permission for the launching terminal, a uniquely named target chat open in WeChat, and an empty draft.

`send --dry-run` constructs and verifies a native message without queuing it. Real send receipts distinguish acceptance by the local client from server acknowledgement; a queued message is not a delivery guarantee.

Each send gets an idempotency key, printed before the API call. **Reuse the same `--key` and the same content when retrying.** The private SQLite ledger retains results for 24 hours, including uncertain outcomes, across service restarts. An interrupted attempt is not automatically resent. Inspect WeChat history before retrying an uncertain send with another key. Keys are scoped to the active account and operation.

OCR uses WeChat's local recognizer. `transcribe` explicitly initiates WeChat voice recognition, which can use WeChat's servers; `transcript` only reads existing recognition state. Downloading uncached stickers can contact supported Tencent CDNs. Native logout, independent QR login, legacy Web WeChat and static reverse-engineering catalogs are not part of this Python gateway.

## REST API and Python client

The server uses FastAPI and a single Uvicorn worker, with Pydantic input validation and generated OpenAPI. The default server is `http://127.0.0.1:8787`. It only binds to loopback. Every API request requires the token in:

```text
~/Library/Application Support/wechat-cli/api-token
```

The browser obtains a local, HttpOnly, SameSite=Strict session after a same-origin check. Browser sessions never expose the API token. No permissive CORS headers are sent. Runtime files and tokens remain outside uv's disposable package cache.

| CLI command | HTTP API |
| --- | --- |
| `status` | `GET /api/native/status` |
| `chats` | `GET /api/native/chats` |
| `messages CHAT_ID` | `GET /api/native/messages?chatId=…&limit=50` |
| `send` | `POST /api/messages/send` |
| `send --dry-run` | `POST /api/messages/prepare` |
| `send-file` / `send-voice` | `POST /api/messages/file` / `/api/messages/voice` |
| `media` | `GET /api/native/media?chatId=…&id=…&variant=full` |
| `download` | `POST /api/native/attachments/download` |
| `ocr` | `POST /api/native/images/text` |
| `transcribe` / `transcript` | `POST /api/native/voice/transcribe` / `GET /api/native/voice/transcript` |
| `stop` | `POST /api/server/stop` |

The service also provides `/api/health`, `/api/capabilities`, `/api/openapi.json`, `/api/native/controls`, and the `/api/chats` and `/api/messages` aliases. The generic `api` command can call any route:

```sh
uvx wechat-cli api /api/health
uvx wechat-cli api /api/messages/send --method POST \
  --json '{"chatId":"filehelper","text":"Hello"}' --key demo-message-002
```

```python
from wechat_cli.client import ApiClient
from wechat_cli.core.config import data_directory

client = ApiClient(data_directory())
chats = client.chats()
history = client.messages("filehelper", limit=20)
receipt = client.send("filehelper", "Hello", idempotency_key="python-demo-001")
```

Select the service state location and port with `--data-dir /absolute/path --port 8788`. Only one bridge server and one WeChat app instance are allowed per user, including across data directories. A second stock or copied WeChat instance causes a clear command-line error; an existing matching instance can be attached instead of launching another. If another copy appears during a session, the bridge service shuts down without killing either app.

Closing the native WeChat main window, quitting WeChat, or losing the native bridge shuts down the server, releases its socket and locks, and closes its worker and ledger. Hiding or minimizing WeChat preserves the service. Login-window transitions and auxiliary windows are handled separately. The web page detects shutdown, disables controls, and asks you to restart and reload; it never automatically retries sends. `stop` and Ctrl-C also shut down the service while leaving the WeChat app signed in. Stop it before deleting uv caches or removing the installed package environment. Setup/doctor are local administration operations; chat/media commands go through HTTP.

## Package architecture

One package provides the CLI, API and plain JavaScript web interface. Platform code is selected lazily; importing the CLI does not import macOS native modules.

```text
src/wechat_cli/
  cli.py, client.py         # CLI and Python HTTP client
  api/                      # FastAPI app, routes, input schemas and local security
  core/                     # Backend contract, shared workflows, models, media and idempotency
  runtime/                  # Uvicorn lifecycle and bounded subprocess RPC transport
  host/                     # OS paths, locks and detached-process primitives
  backends/macos/           # Setup, Frida worker, native adapter, Swift/C++ assets
  compatibility/macos-arm64/ # Validated payload fingerprints and native signatures
  web/                      # Static HTML, CSS and JavaScript
```

`core/contracts.py` defines the operations a backend provides. HTTP handlers and media workflows call this contract rather than native RPC method strings or platform controls. The app lifespan owns the backend, idempotency ledger, global instance lock and liveness monitor. Native calls stay in a subprocess with a versioned startup handshake and bounded requests; a disconnected worker cannot relaunch WeChat during an active session.

Only Apple Silicon macOS is currently validated. Windows host primitives are separated, but a native Windows backend remains unimplemented; Windows and Linux return an explicit unsupported-platform error. Add a validated backend and compatibility profiles behind the same contract to extend support. Do not copy macOS ABI assumptions into another backend.

## Development and release

From this folder:

```sh
uv sync --group dev
uv run pytest
uv run ruff check src tests
uv run ruff format --check src tests
uv build --no-sources
uv run twine check dist/*
uvx --from ./dist/wechat_cli-0.1.0-py3-none-any.whl wechat-cli --help
```

Before publication, run the built wheel from outside the repository and execute the opt-in read-only live smoke check with a signed-in prepared app. See [docs/validation.md](docs/validation.md) for this refactor's checks and limitations. Tests never send real messages.

Publish the reviewed artifacts with your PyPI credentials or a configured trusted publisher:

```sh
uv publish dist/wechat_cli-0.1.0-py3-none-any.whl dist/wechat_cli-0.1.0.tar.gz
```

The distribution name and console entry point are both **`wechat-cli`**; the import name is `wechat_cli`. Name availability is not a reservation. Publication is a separate step and is not performed by setup or the test suite. The Python server is independent of the existing `wechat-node` implementation.
