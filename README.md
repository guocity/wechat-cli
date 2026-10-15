# wechat-cli

A Python CLI, authenticated local REST API, Python client and web interface for a signed-in macOS or Windows WeChat session. Node.js and npm are not required. `uvx` automatically selects dependencies for the current operating system and installs them into its own environment. No `[mac]`/`[win]` extra or platform prompt is needed.

## Quick start

After publication to PyPI:

```sh
uvx wechat-cli setup
uvx wechat-cli start
uvx wechat-cli chats
uvx wechat-cli web
```

Dependency selection uses standard Python environment markers during installation. Both platforms install FastAPI, Uvicorn, OS TLS trust and Frida. macOS additionally installs Cryptography, Pillow and the SILK codec. Windows omits those media packages; Python 3.11–3.13 adds Zstandard, while Python 3.14+ uses the standard library. Runtime imports of macOS media libraries are lazy, so Windows can start its server without them. Developer test dependencies are separate and are not installed by `uvx`.

The same small wheel contains both backend modules and their source assets; platform markers select dependencies, not wheel files. No vendor app or native helper binary is bundled. Separate platform distributions would add release and version coordination without materially reducing this wheel.

On macOS, `setup` downloads WeChat 4.1.15 from Tencent and Frida Gadget 17.8.0 from the upstream Frida release. It verifies the Gadget archive's SHA-256, the downloaded app's Tencent signature and its supported payload fingerprint, mounts the installer read-only, and prepares a separate app in:

```text
~/Library/Application Support/wechat-cli/apps/WeChatBridge.app
```

It adds Gadget, builds the bundled C++ helper, signs the copy, and records its location. `/Applications/WeChat.app` is not modified. No WeChat app, Gadget binary, credentials or account data are redistributed in the Python package.

macOS requires **Apple Silicon**, **Python 3.11+** (managed by uv when necessary), **uv**, and **Xcode command-line tools** (`xcode-select --install`). The native helper and SILK codec require a compiler. Setup runs as your normal user; do not use `sudo uvx`.

On **Windows x64**, the same commands download Tencent's verified **4.1.15.53** installer, extract its portable app using Windows `tar.exe`, and prepare `%LOCALAPPDATA%\wechat-cli\apps\WeChat-4.1.15.53`. Python 3.11+ and uv are required; native chat/history/text sending needs no compiler or external 7-Zip. The bridge attaches Frida to the verified main process and preserves the signed vendor binaries. Run as the signed-in desktop user. For SSH launches, the same user must also have an interactive Windows session.

Windows supports native chats, history and text sending through the CLI, API and web interface. Media downloads/previews, file/voice sending, OCR and voice recognition currently require macOS; `/api/capabilities` reports this and the web interface hides unsupported controls. Windows 11 x64 is live-tested; ARM Windows and Linux are unsupported.

On macOS CLI startup, the bridge automatically accepts WeChat's exact **“opened from the installation package”** location notice with its **“Do not show this message again”** box selected. This policy is installed before the app resumes; setup itself does not need Accessibility permission to configure it.

Quit other WeChat copies before starting the prepared app. Sign in through its normal WeChat window if needed. `start` launches that app and a persistent FastAPI/Uvicorn service. Later commands call the service and reuse its session; they do not log in again. `web` also starts the service if needed, then opens its local page. `stop` stops the Python service and leaves WeChat running and signed in.

Before a new server starts, it checks the registered bridge against the package's latest validated release, currently **4.1.15 / build 270135** on macOS and **4.1.15.53** on Windows. A registered 4.1.13 copy is automatically upgraded: startup reuses a verified managed 4.1.15 copy if available, otherwise downloads and prepares a separate versioned copy, then atomically switches the runtime configuration before launching it. Upgrade progress is printed to stderr. The older copy and `/Applications/WeChat.app` are preserved; the previous runtime pointer is saved as `previous-native-runtime.json`.

If WeChat is already running when an upgrade is needed, quit it and run `start` again. Startup enforces the single-instance rule and does not force-quit an app or replace a running bundle. A failed download, unexpected payload or preparation failure leaves the old runtime configuration intact and reports an error instead of silently starting the older version. Already-current copies require no update download. An existing running API session is reused; the check runs on the next new server startup, including `serve` and `web`.

## Supported payloads

| macOS WeChat version | Build | Installer |
| --- | --- | --- |
| 4.1.13.34 | 269602 | Supply an existing compatible app with `--source` or `--app` |
| 4.1.13 | 269631 | Tencent's current `WeChatMac_4.1.13.dmg` |
| 4.1.15 | 270135 | Tencent's current `WeChatMac_4.1.15.dmg` (default) |

Profiles are selected by the entire vendor payload's SHA-256, with native function signatures checked before use. Installer filenames alone do not establish compatibility. Tencent can replace a download without changing its filename. Future payloads require a new validated profile and package release; unknown payloads are rejected before patching. To get the newest package, use `uvx --refresh wechat-cli setup`.

To prepare the current 4.1.13 download for compatibility work (normal server startup subsequently selects the latest validated release):

```sh
uvx wechat-cli setup \
  --wechat-url https://dldir1v6.qq.com/weixin/Universal/Mac/WeChatMac_4.1.13.dmg \
  --destination "$HOME/Applications/WeChatBridge-4.1.13.app"
```

On macOS, to use a local installer app or an already patched copy:

```sh
uvx wechat-cli setup --source /Applications/WeChat.app
uvx wechat-cli setup --app /absolute/path/to/WeChatBridge.app
uvx wechat-cli doctor
```

`--source` still requires a supported payload. `--gadget /path/to/FridaGadget.dylib` uses a locally supplied Gadget instead of downloading it. `--destination` must be a new path outside the source app. Setup never overwrites an existing app; `--app` registers and rebuilds an existing patched copy. Repeated default setup reuses the previously prepared copy.

On Windows, register an existing verified extracted copy with `uvx wechat-cli setup --app 'C:\path\to\WeChat'`, or use `--source` to copy it into managed storage. A normal `setup` downloads and verifies the official installer automatically. `--gadget` is macOS-only.

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

On Windows the token is `%LOCALAPPDATA%\wechat-cli\api-token`; state directories, tokens and the request ledger use protected user/SYSTEM/administrator ACLs.

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

Closing the native WeChat main window, quitting WeChat, or losing the native bridge shuts down the server, releases its socket and locks, and closes its worker and ledger. Minimizing WeChat preserves the service. On Windows, closing or hiding the main window to the system tray stops the server; `start` reopens the same app. macOS Hide preserves the service. Login-window transitions and auxiliary windows are handled separately. The web page detects shutdown, disables controls, and asks you to restart and reload; it never automatically retries sends. `stop` and Ctrl-C also shut down the service while leaving the WeChat app signed in. Stop it before deleting uv caches or removing the installed package environment. Setup/doctor are local administration operations; chat/media commands go through HTTP.

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
  backends/windows/         # Verified portable setup, native SQLite/send adapter, Frida agent
  compatibility/windows-x64/ # Validated Windows payload fingerprints and native signatures
  compatibility/macos-arm64/ # Validated payload fingerprints and native signatures
  web/                      # Static HTML, CSS and JavaScript
```

`core/contracts.py` defines the operations a backend provides. HTTP handlers and media workflows call this contract rather than native RPC method strings or platform controls. The app lifespan owns the backend, idempotency ledger, global instance lock and liveness monitor. Native calls stay in a subprocess with a versioned startup handshake and bounded requests; a disconnected worker cannot relaunch WeChat during an active session.

`compatibility/macos.json` and `compatibility/windows.json` pair the latest validated native profile with its installer URL. Startup upgrades use this shipped manifest and verify the exact payload fingerprint; they do not guess compatibility with an untested vendor update. Background-start commands finish downloads/builds in the foreground before starting the native-startup timeout, and foreground `serve` performs the same check through the backend.

Live Windows evaluation covers signed-in chat/history reads, native text acceptance, an outgoing File Transfer record and durable same-key deduplication. See [Windows validation](docs/windows-validation.md) for exact results and limitations. Unknown payloads are rejected; supporting a future WeChat release requires validating a new native profile. Linux can add another backend behind the shared contract without changing the API or GUI.

## Development and release

From this folder:

```sh
uv sync --group dev
uv run pytest
uv run ruff check src tests
uv run ruff format --check src tests
uv build --no-sources
uv run twine check dist/*
uvx --from ./dist/wechat_cli-0.2.0-py3-none-any.whl wechat-cli --help
```

Before publication, run the built wheel from outside the repository and execute the opt-in read-only live smoke check with a signed-in prepared app. See [docs/validation.md](docs/validation.md) for this refactor's checks and limitations. Tests never send real messages.

Publish the reviewed artifacts with your PyPI credentials or a configured trusted publisher:

```sh
uv publish dist/wechat_cli-0.2.0-py3-none-any.whl dist/wechat_cli-0.2.0.tar.gz
```

The distribution name and console entry point are both **`wechat-cli`**; the import name is `wechat_cli`. Name availability is not a reservation. Publication is a separate step and is not performed by setup or the test suite. The Python server is independent of the existing `wechat-node` implementation.
