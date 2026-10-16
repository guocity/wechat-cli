# Windows native backend validation

Test host: Windows 11 x64, WeChat **4.1.15.53**, 2026-10-15. The checkout is `C:\Users\owner\Documents\github\wechat-cli`. Tests use the signed-in desktop user through SSH/RDP; no firewall, Defender, UAC or SSH settings are changed.

## Supported operations and limits

The CLI, authenticated REST API, Python client and web interface support conversations/history, text/file/voice sending, cached media previews/downloads, local OCR and native voice recognition. `/api/capabilities` advertises these operations. ARM Windows and unknown WeChat payloads are unsupported.

- Media reads use the active account's native SQLite handles and hardlink records. V1/V2 `.dat` headers are decrypted inside WeChat, and its bundled codec converts `wxgf` images to JPEG. Database and image keys are not exported. Cache scans, file sizes and decoded results are bounded, and attachment bytes must match their message's digest and size. Download uncached attachments in WeChat first.
- File and voice sends use native shared objects and WeChat's asynchronous message queue. Receipts verify recipient, UUID and local message-service acceptance; they do not claim server delivery acknowledgement. Outgoing files are retained temporarily for asynchronous upload. The existing idempotency ledger prevents dispatching the same request key again.
- SILK encoding/decoding uses the exact bundled `VoipEngine.dll`, checked against its profile hash, in a disposable child process. Windows CryptoAPI handles AES. These operations require neither `pilk` nor compiled Python media packages, including on Python 3.15.
- OCR uses WeChat's installed `WeChatOcr` plugin through `mmmojo_64.dll`. Install/initialize the plugin in WeChat if it is missing. Recognition runs locally in a disposable process. That process announces its actual interpreter PID and waits for the server to register ownership before launching `Weixin.exe --type=wxocr`. This handles Windows venv/uv launcher stubs and prevents false single-instance shutdowns; another unrelated WeChat process still fails the instance check. Process inspection also drops confirmed exited processes from stale Toolhelp snapshots while retaining fail-closed handling for access-denied or otherwise unidentified live processes.
- `transcribe` explicitly starts native recognition, which can use WeChat's servers. `transcript` reads recognition state without starting conversion. Recognition runs within a valid vendor scheduler context; a replacement guard preserves constructor/completion observation. Completed/pending jobs are reused. A new idempotency key permits an explicit retry after a finished failure; a timed-out task still running natively is not duplicated. Results are held in the current bridge session. The web interface exposes the voice controls through `voiceToText: "wechat_native_task"`.

## Live checks

The signed-in copy returned 102 conversations. Earlier authorized File Transfer checks confirmed native text, a labeled file attachment and a one-second generated tone as outgoing records. File-send same-key retries returned the stored receipt without another native dispatch. Later regression runs reuse these records and send no additional messages.

Cache image decoding, native `wxgf` rendering, digest-verified attachment download, SILK encode/decode and WAV voice previews were exercised on Windows. Synthetic OCR fixtures verify recognizable public text without exporting conversation contents. Live OCR regression runs keep Uvicorn's native liveness monitor active while helpers start and exit. Transcription checks use the existing generated tone: a recognition failure is expected for non-speech audio and does not establish speech-recognition accuracy.

The reported-fix live regression passed on Python 3.11.17: three OCR requests completed while the 50 ms liveness monitor observed the owned helper (16 checks during recognition), and the API remained healthy. Transcription returned `pending`, then `failed` for the tone; repeating the same request key returned the original receipt, while a new key started another native task and reached completion. Existing voice WAV preview and the 55-byte test attachment download also passed. The isolated test server detached cleanly afterward.

The installed wheel also passed the same live regression on **Python 3.15.0**: three OCR requests with 14 monitor checks during recognition, native task completion and explicit retry, WAV preview and attachment download. Native AES decryption and a one-second 24 kHz PCM → SILK → PCM round trip passed with `pilk` absent. This run additionally reproduced and fixed a Toolhelp exit race: a process that terminates during inspection must not become an unidentified extra instance. Four regressions distinguish confirmed exits from access denial and unidentified live processes.

Native mappings were checked against the exact DLL using PE unwind boundaries, disassembly, bounded decompilation and live checks. No third-party hook implementation or vendor binary is bundled.

## Automated checks and packaging

```powershell
python -m uv sync --python 3.11 --group dev
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe tools\windows_live_smoke.py --data-dir validation\windows\state --port 8789
```

The smoke tool is read-only unless `--send-file-transfer` is explicitly supplied. That flag sends a real message and requires authorization.

Current Python regressions: **Windows 152 passed, 1 skipped; macOS 151 passed, 2 skipped**. Platform-specific skips cover Windows CryptoAPI or macOS POSIX permissions. Tests cover API validation/authentication, durable idempotency, Unicode transport, native process identity, the OCR launcher/interpreter PID handshake, account-scoped cache access, digest checks, codecs and transcription retry behavior. Mocked unit results are separate from live native checks.

Three additional JavaScript regressions cover nested constructor observation, read/retry semantics, and restoring the original scheduler guard on disposal:

```sh
node --test tests/windows-transcription.test.cjs
```

Node is optional for this development check and is not a runtime dependency. `ruff check`, `ruff format --check`, wheel construction and `tools/check_wheel.py` validate the source and distributable. The wheel check requires all Windows helper modules and both native agent scripts, and rejects vendor DLLs, executables and private state.

## Setup and process lifecycle

`uvx wechat-cli setup` downloads Tencent's [4.1.15 installer](https://dldir1v6.qq.com/weixin/Universal/Windows/WeChatWin_4.1.15.exe), verifies its SHA-256 and Tencent Authenticode signature, extracts the embedded payload with Windows `tar.exe`, verifies the EXE/DLL hashes and publishes the prepared directory atomically. Signed vendor PE files are not modified. An unknown or changed payload fails closed.

```text
installer:  aa727e6cc74cd7c4e101c350d125508b0230a8a19c1f5304bf6f33dde2e758c5
Weixin.exe: 15f1c3d8118447aa89d917bf0a3c0743930be500ead8ea65a1d7296a66fee1fc
Weixin.dll: 88eabf78f961e0abe26b397521b05f4ba6cd752727fb9db74a2b3f660b0400f2
```

State directories, tokens, locks and the request ledger use protected NTFS DACLs for the current user SID, SYSTEM and Administrators. Queries borrow native database connections, bind parameters and serialize bounded read-only operations, including vendor NOMUTEX handles. Account changes invalidate connection selection.

Only one matching main WeChat instance may be attached. Recognized helpers require the matching executable and an owned parent; foreign or inaccessible main processes remain errors. Native window monitoring runs inside the desktop process so SSH-launched servers can observe it. Minimizing preserves the service; closing/hiding the main window stops it. Startup can restore the previously observed main window bound to its PID and payload hash. `stop` detaches without signing out or terminating WeChat.

Platform dependency markers automatically omit Pillow, Cryptography and pilk on Windows. Python 3.11–3.13 uses Zstandard's wheel; 3.14+ uses standard-library Zstandard. macOS retains its media dependencies. The installed wheel was verified outside the source environment on Windows Python 3.15, including the feature-specific checks above.
