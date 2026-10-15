# Windows validation

Validated on a Windows 11 x64 machine on 2026-10-15. The working checkout is `C:\Users\owner\Documents\github\wechat-cli`, on `codex/windows-validation`, copied from commit `557ee61` with the accompanying portability fixes. SSH was already enabled; no SSH or firewall settings were changed.

## Shared package

`uv sync --python 3.11 --group dev` installs the project without a compiler. The existing machine Python was 3.15. A Python 3.13 attempt failed because `pilk==0.2.4` has Windows wheels only through Python 3.11; newer Python requires Microsoft C++ Build Tools. Use Python 3.11 for Windows evaluation until the codec packaging is changed.

```powershell
python -m uv sync --python 3.11 --group dev
.venv\Scripts\python.exe -m pytest -q
```

Result: **102 passed, 1 skipped**. The skip is specifically the macOS destination-parent POSIX permission test. POSIX mode-bit assertions are conditional on the host; their functional token/configuration/idempotency assertions still run on Windows. This does not validate Windows ACL isolation: Windows `chmod` does not implement POSIX private permissions, and the Windows backend remains disabled pending an ACL implementation.

The suite covers authenticated HTTP requests, browser sessions and origin checks, CLI-to-HTTP calls, durable idempotency, real SILK encode/decode, subprocess transport, server shutdown after backend/GUI loss, and instance locks. Its API backend is a test double; these are not live Windows chat/send tests. Additional subprocess tests prove lock contention and release across processes using the real Windows lock implementation. Unicode configuration writes and worker responses use explicit UTF-8, and configuration writes use consistent LF newlines.

macOS regression result: **103 passed**; the 11 host/worker tests were rerun after the final newline fix.

The built wheel was also installed using `uv tool run --python 3.11 --from <wheel> wechat-cli --version` from outside the checkout; it returned `0.1.0`. `doctor` returned the intended `unsupported_platform` error with exit code 1. Ruff passed on Windows. The temporary launch task and owned test app were stopped after testing; the extracted vendor files and diagnostic logs remain under the ignored `validation\windows` directory.

## Vendor app and instrumentation

Downloaded the supplied [Tencent installer](https://dldir1v6.qq.com/weixin/Universal/Windows/WeChatWin_4.1.15.exe) directly on Windows. Windows `Get-AuthenticodeSignature` reported **Valid**, with signer Tencent Technology (Shenzhen) Company Limited, for both the installer and extracted launcher. The installer hash matches the earlier static investigation:

```text
installer:  aa727e6cc74cd7c4e101c350d125508b0230a8a19c1f5304bf6f33dde2e758c5
Weixin.exe: 15f1c3d8118447aa89d917bf0a3c0743930be500ead8ea65a1d7296a66fee1fc
Weixin.dll: 88eabf78f961e0abe26b397521b05f4ba6cd752727fb9db74a2b3f660b0400f2
```

Official 7-Zip 26.04 extracted `install.7z`, then its complete payload, under `validation\windows\WeChat`. No normal WeChat installation was present. The extracted app **4.1.15.53** launched successfully in the interactive RDP desktop and displayed its QR login window. No account was signed in and no messages were sent. A temporary interactive scheduled task started the GUI because SSH launches use a different Windows session.

`tools/windows_native_probe.py` attached Frida **17.8.0** to that running process, loaded a script, enumerated and fingerprinted its Weixin EXE/DLL, then detached successfully. It reported `x64` / `windows`. It does not call vendor functions, read chats, modify binaries, or enable the Windows backend. Run it against a manually launched test copy:

```powershell
.venv\Scripts\python.exe tools\windows_native_probe.py `
  --pid <WEIXIN_PID> --exe validation\windows\WeChat\Weixin.exe `
  --output validation\windows\native-probe.json
```

This establishes that a separate extracted Windows copy can start and accept Frida attachment on this machine. It does not prove portability across other Windows versions or security configurations. Native Windows account/session/history/send mapping, x64 ABI helpers, real GUI-close lifecycle handling, process checks, setup integration, and private state ACLs remain required before enabling Windows as a supported backend. The registry continues to reject Windows explicitly.
