# Release notes

## 0.2.4

- WeChat Channels publishing functionality on macOS: manage the publishing lifecycle on the Channels page, handle video uploads, cover frames, options, and publishing session state via dedicated API routes and client methods.

## 0.2.3

- Channels media resolution and video playback: direct native loading via compiled C++ helper on the Finder DedicatedWorker, with object-bound media tokens and secure decryption.
- Automatic login behavior and Channels video recovery tests.
- Extended Moments feed inspection with limit and pagination options.

## 0.2.2

- Native Moments composer workflow on macOS (`wechat-cli moments post`, API routes, and web interface controls).
- Windows media support: implement AES decryption via Windows CryptoAPI, image dimension validation for PNG/JPEG, local media caching via hardlinks, and local OCR/transcription primitives.

## 0.2.1

- Reset Windows application directory permissions on extraction, doctor, and startup so Task Scheduler launches under limited user tokens succeed without access-denied errors.
- Gracefully skip SILK codec tests when pilk is not installed on Windows Python 3.15.

## 0.2.0

- Shared FastAPI/Uvicorn API, CLI and web interface with native macOS and Windows backends.
- Automatic platform dependency selection with the same `uvx wechat-cli` command.
- Verified Windows 4.1.15.53 setup, chat/history reads and native text sending.
- Automatic upgrades to the latest validated managed WeChat copy before startup.
- One WeChat instance per user, with server shutdown tied to native GUI/bridge closure.
- Preserve Windows history ordering across shards and promptly fail pending calls on GUI closure.

Windows media, voice and OCR remain unavailable. macOS requires Apple Silicon; Windows requires x64. Unknown WeChat payloads are rejected. See [validation](validation.md) and [Windows validation](windows-validation.md) for live coverage and limits; final review fixes have unit coverage but no new live Windows run.
