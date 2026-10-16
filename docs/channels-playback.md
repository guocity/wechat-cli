# Channels playback and MP4 download on macOS

The web viewer displays covers automatically and supports **Play video** and
**Download MP4** for Channels cards shared in chat. Plain `uvx wechat-cli` uses
the configured app and existing local API; no proxy or separate backend is used.

## Use

1. Open the conversation in the local viewer. Click **Play video** or
   **Download MP4**. For uncached media, the approved WeChat research app opens
   that exact card through the conversation’s Channels history automatically.
2. Keep WeChat running while its player loads and the local viewer recovers the
   MP4. Initial recovery can take several seconds.
3. The validated MP4 is cached privately per account and message. Later playback
   and downloads work from cache, including after restarting WeChat.

This recovers the original compressed H.264/AAC media; it does not record the
screen or re-encode the video. A CDN-provided filename is preserved if available.
Otherwise the download is named `channels-<objectId>.mp4`; that fallback is not
an original filename supplied by WeChat.

The existing CLI `media CHAT_ID MESSAGE_ID --output PATH` also uses recovery.
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
It uses Frida's `code_signing: required` mode and fault-tolerant read-only memory
reads. It does not use Interceptor or modify executable pages at runtime.

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

Recovery accepts only a playback URL whose encrypted-file key, digest query
parameter and path match the requested message, on an allowed WeChat CDN. Signed
URLs are kept in memory and are not persisted in the MP4 cache or sent to the page.
The decrypted 128 KiB prefix is accepted only when the following 64 bytes match
that exact downloaded file and the resulting complete MP4 box layout validates.
Searches have byte/time bounds and verify the renderer's process identity and
configured app path. Cache files are account/message scoped with private permissions.

Live validation on 2026-10-16: the newly forwarded File Transfer card produced a
16,258,283-byte MP4 with H.264 video, AAC audio, 576×1024 dimensions and duration
258.342993 seconds. A full ffmpeg decode reported no errors. The local browser
reported readyState 4, advancing currentTime, paused false and no media error.
The downloaded file's SHA-256 matched the validated recovery:
`f3a1109ac06adfa3a72b0fe1779c76bdc5e3dbadd28960e98b30ea5ea5076f8d`.
After moving to the managed app and restarting, the cached media endpoint returned
that same file in approximately 0.14 seconds.
