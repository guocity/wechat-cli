# Windows native backend validation

Test host: Windows 11 x64, 2026-10-15. The checkout is under `C:\Users\owner\Documents\github\wechat-cli`. Testing uses the same signed-in Windows user over an existing SSH/RDP session; no firewall, Defender, UAC or SSH settings were changed.

## Supported native operations

The Windows backend supports WeChat **4.1.15.53**: conversation listing, recent local history, text preparation and text sending. The shared FastAPI/Uvicorn server, Python client, CLI and web interface use this backend through the existing contract. Media previews/downloads, file/voice sending, OCR and voice recognition remain unavailable on Windows and are advertised accordingly by `/api/capabilities`.

Live verification found **102 conversations**. A preparation request verified the recipient, Unicode content, native UUID and the vendor object's own type/subtype getters without dispatching. One explicitly authorized labeled File Transfer test was accepted by the native message service and appeared exactly once as an outgoing Unicode history record. Repeating its API request with the same key returned the stored receipt without another native dispatch. Local acceptance does not imply server delivery acknowledgement. An earlier diagnostic attempt was unconfirmed and was not retried.

The agent borrows the active account's own SQLite connections inside the owning WeChat process. Queries use bound parameters, read-only statements, bounded results and per-connection serialization, including guards for vendor NOMUTEX connections. Account changes invalidate connection selection. It does not extract database keys or copy account databases. Text dispatch uses native shared objects and the native asynchronous scheduler. A completion hook matches the request UUID to the native message-service result; dispatch alone is insufficient for an accepted receipt. A missing completion is an uncertain outcome retained by the existing idempotency ledger.

## Setup and payload verification

`uvx wechat-cli setup` downloads the supplied [Tencent installer](https://dldir1v6.qq.com/weixin/Universal/Windows/WeChatWin_4.1.15.exe), verifies its exact SHA-256 and Tencent Authenticode signature, extracts its embedded 7z payload with Windows `tar.exe`, checks the complete EXE/DLL hashes and launcher signature, then publishes the prepared directory atomically. Fresh automatic download/extraction and startup were verified. Windows uses runtime Frida attachment; signed vendor PE files are not modified. Vendor binaries and private state are excluded from the wheel.

```text
installer:  aa727e6cc74cd7c4e101c350d125508b0230a8a19c1f5304bf6f33dde2e758c5
Weixin.exe: 15f1c3d8118447aa89d917bf0a3c0743930be500ead8ea65a1d7296a66fee1fc
Weixin.dll: 88eabf78f961e0abe26b397521b05f4ba6cd752727fb9db74a2b3f660b0400f2
```

Native mappings were checked against this exact DLL using PE unwind boundaries, disassembly, bounded Ghidra decompilation and live native checks. No third-party hook implementation is bundled. A changed Tencent download or unknown payload fails closed and needs a newly validated profile and package release.

## Automated and packaging checks

```powershell
python -m uv sync --python 3.11 --group dev
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe tools\windows_live_smoke.py --data-dir validation\windows\state --port 8789
```

The smoke tool is read-only unless `--send-file-transfer` is explicitly supplied. Do not use that flag without permission to send a real message.

Windows: **114 passed, 1 skipped**. The skip checks macOS destination-parent POSIX permissions. macOS: **114 passed, 1 skipped**. Coverage includes HTTP authentication, browser origin/session checks, validation, durable idempotency, Unicode subprocess transport, server shutdown, instance locks, payload tamper detection, bound history queries, exact integer IDs and failed-upgrade rollback. These tests use a fake backend; the live native results above are separate.

The wheel installs and runs outside the checkout on macOS and Windows, including Windows Python **3.15**, without a compiler for core operations. Windows Python 3.12+ omits the unused SILK codec; Python 3.14+ uses standard-library Zstandard decoding. Windows Python 3.11 uses the available codec and Zstandard wheels. Wheel contents, metadata and entry point pass `tools/check_wheel.py` and Twine validation.

State directories, tokens, locks and the request ledger use protected NTFS DACLs for the current user SID, SYSTEM and Administrators. The live API token's ACL was checked for protected inheritance and those principals; POSIX `chmod` is not used as an NTFS privacy guarantee.

Only a matching main WeChat process may be attached. Recognized helper children with the same executable path and a matching parent are excluded from the instance count. Another root, another path or an inaccessible process produces the explicit single-instance error. Native window monitoring runs inside the desktop process so an SSH-launched server can observe it across Windows sessions. `stop` detaches the bridge without logging out or terminating WeChat.

Closing the fresh native login window stopped Uvicorn and released the API port. Starting again reopened WeChat; that close action terminates the vendor process, so a new PID is expected. Main-window restoration records only a previously observed window, bound to the PID and payload fingerprint, rather than displaying arbitrary hidden Qt windows. The explicit user SID ACL also allows a managed app prepared through elevated SSH to launch with the same user's limited desktop token.

The fresh signed-in managed copy returned 102 chats and the single previously verified File Transfer record. A native minimize/restore preserved the service. Hiding its main window stopped the service; startup restored the saved main window in the same signed-in process. No additional messages were sent for these checks.

The built wheel was also run from outside the repository using `uv tool run --python 3.15 --from <wheel> wechat-cli start`. Its Python 3.15 server passed the same read-only 102-chat/one-history-record/native-preparation smoke check and shut down through the installed CLI. A standard-library Zstandard compression/decode round trip also passed. Test APIs were stopped afterward; WeChat remained signed in.
