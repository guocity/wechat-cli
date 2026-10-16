# Channels playback and MP4 download on macOS

The web viewer displays covers automatically and supports **Play video** and
**Download MP4** for Channels cards shared in chat. Plain `uvx wechat-cli` uses
the configured app and existing local API; no proxy or separate backend is used.

## Use

1. Open the conversation in the local viewer. Click **Play video** or
   **Download MP4**. The existing background Finder worker resolves that exact
   card using your signed-in WeChat session.
2. The CLI downloads the signed media and decodes its encrypted 128 KiB prefix
   with the native response's ISAAC64 key. No card/player window opens and no
   playback is required. WeChat must remain running and signed in.
3. The validated MP4 is cached privately per account and message. Later playback
   and downloads work from cache, including after restarting WeChat.

This recovers the original compressed H.264/AAC media; it does not record the
screen or re-encode the video. A CDN-provided filename is preserved if available.
Otherwise the download is named `channels-<objectId>.mp4`; that fallback is not
an original filename supplied by WeChat.

The existing CLI `media CHAT_ID MESSAGE_ID --output PATH` also uses native resolution.
Arbitrary share-URL resolution is not implemented. Unavailable or unsupported
cards produce a clear error instead of substituting an unrelated cached clip.
Only the validated macOS app version/build and renderer fingerprint are currently
supported by the research setup tool.

## Research build

The runtime uses a separate, explicitly approved development copy. The stock
installation and original prepared bridge remain intact. AppEx and its five
helpers retain their sandbox entitlements and get app-specific library-validation
exceptions; only the renderer gets the debugging entitlement. No SIP, global
security policy, network proxy, or privacy-permission changes are made.

Signed renderer instrumentation listens only on authenticated loopback endpoints.
It uses Frida's `code_signing: required` mode, fault-tolerant metadata reads,
and a precompiled, signed task helper. It does not use Interceptor or modify executable pages at runtime.

Reproduce the build from an existing supported prepared app:

```sh
uv run --project wechat-cli python wechat-cli/tools/prepare_channels_playback.py \
  --source "/path/to/WeChatBridge.app" \
  --destination "/path/to/WeChatChannelsResearch.app"
```

The command applies the documented research exceptions only to a new copy,
checks the renderer SHA-256, and verifies deep signatures. It neither launches
nor registers that copy. After quitting the existing instance, use the normal
`wechat-cli setup --app "/path/to/WeChatChannelsResearch.app"` to select it.

For this installation, the managed research copy is under the CLI data directory's
`apps/WeChatChannelsResearch.app`. The previous runtime configuration is preserved
in `channels-original-native-runtime.json`. To revert, quit the research instance
and register the original `apps/WeChatBridge.app` with `setup --app`, then start.

## Correlation and validation

The signed helper schedules a normal non-nestable task on WeChat's existing
Finder worker. It enters that worker's V8 context and uses the shipped native
`finderH5ExtTransfer` request envelope. Only our unique callback ID is intercepted;
all other callbacks delegate unchanged, and the wrapper is restored on completion,
failure or cancellation. No runtime executable-page patching is used.

Resolution requires the exact message's object ID and nonce. The returned object
ID must match before any download. URL, URL token and decode key must come from
the same returned media item. Only approved WeChat CDN hosts are accepted, including
redirects. The smallest advertised H.264 rendition is selected for browser playback.
URLs and decoding keys stay in memory and are never sent to the web page or cached.
Decoded bytes must form a complete MP4 before playback/download or cache insertion.
The native ABI is pinned to the supported arm64 WeChat 4.1.15 build; signatures,
renderer path, process identity, task runner and context are checked before use.

Live validation on 2026-10-16: direct native resolution, download and decoding of
the explicitly forwarded File Transfer card completed in **3.25 seconds**, producing
a 16,258,283-byte MP4 with H.264 video, AAC audio, 576×1024 dimensions and duration
258.342993 seconds. No native player was opened or played. The SHA-256 matched the
previous validated recovery:
`f3a1109ac06adfa3a72b0fe1779c76bdc5e3dbadd28960e98b30ea5ea5076f8d`.

After installing the updated CLI and restarting the signed research app, the exact
card's private MP4 cache was moved aside. Clicking Play video in the local browser
loaded the video through the new native path: readyState 4, paused false, advancing
currentTime, no media error. Download MP4 saved the same 16,258,283-byte file; its
SHA-256 matched. A full FFmpeg decode completed without errors. Regression suite:
199 passed, 2 skipped; the app passed deep strict codesign verification.
