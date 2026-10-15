# Release notes

## 0.2.0

- Shared FastAPI/Uvicorn API, CLI and web interface with native macOS and Windows backends.
- Automatic platform dependency selection with the same `uvx wechat-cli` command.
- Verified Windows 4.1.15.53 setup, chat/history reads and native text sending.
- Automatic upgrades to the latest validated managed WeChat copy before startup.
- One WeChat instance per user, with server shutdown tied to native GUI/bridge closure.
- Preserve Windows history ordering across shards and promptly fail pending calls on GUI closure.

Windows media, voice and OCR remain unavailable. macOS requires Apple Silicon; Windows requires x64. Unknown WeChat payloads are rejected. See [validation](validation.md) and [Windows validation](windows-validation.md) for live coverage and limits; final review fixes have unit coverage but no new live Windows run.
