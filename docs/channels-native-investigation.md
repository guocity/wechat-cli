# Direct Channels download investigation (2026-10-16)

**Current result:** Shared-card playback/download now works in the approved
research runtime. See [verified implementation and usage](channels-playback.md).
The observations below record intermediate investigation states; arbitrary
share-URL resolution remains unimplemented.

Required interface: one Channels URL, no separate backend or proxy; reuse WeChat's
signed-in session, download and validate the MP4, preserve the original media
filename when available. Arbitrary share-URL resolution is **not implemented**.
Shared-message media support is described below.

The rejected external `wx_channels_download` API adapter has been removed.
Existing auto-login, Moments listing and composition remain unchanged.

## Observations

- The macOS bridge profile for WeChat 4.1.15 / build 270135 validates native
  chat, message, voice and task-context entry points; it has no validated
  Finder/Channels media descriptor entry point.
- Upstream Channels schemas distinguish encrypted `objectDesc.media` metadata
  (`url`, `decodeKey`, `urlToken`) and shared-feed `feedInfo` video URLs. These
  schemas alone do not provide a callable authenticated resolver in this app.
- The prepared WeChat bundle runs a separate `WeChatAppEx` helper. A read-only
  Frida attach probe against that exact helper returned `PermissionDeniedError`.
  No script was loaded, user data read, or Channels page opened by that probe.
- Bounded static analysis of the vendor image and existing repository fixtures
  did not establish a working native Finder resolver. Failure to find literal
  names is not proof that such functions are absent.
- The earlier requested Channels page was blocked by the browser site-safety
  gate. No alternate route was used to open or download that video.

## Remaining work

Recover and validate an authenticated media-descriptor or native export path
from WeChat's playback component, with exact request/result correlation. Then
implement media transfer/decryption and original filename handling. Do not
substitute an unrelated cached clip, invent undocumented native calls, claim
fixture tests establish live support, or silently enable wider process-debug
access.

The main bridge's Gadget transport is not proof that the separate webview
helper can be instrumented. Re-signing/injecting that helper or enabling its
debugging interface requires a separately reviewed implementation and live
validation; no such signature or security changes were made in this pass.

## Shared-message media support

The CLI media endpoint and web viewer now understand type-51 `finderFeed` cards.
They read the first video from `mediaList/media`, fetch only approved WeChat CDN
hosts, and require a complete MP4 container before returning video bytes. This
uses message metadata directly, without a separate proxy or backend. It does
not resolve arbitrary Channels share URLs or decrypt Channels video.

The viewer loads covers automatically when cards approach the visible area,
and provides Play video and Download MP4 controls. CDN
Content-Disposition filenames are preserved after sanitizing; absent a filename,
it uses `channels-<objectId>.mp4`. This fallback is not an original filename.

Live test: a different card newly forwarded to File Transfer on 2026-10-16
contained `finder.video.qq.com` URLs. Its cover returned HTTP 200 and a validated
72,181-byte JPEG; its video URL returned HTTP 400. Video URL query fields were
`encfilekey`, `hy`, `idx`, `m`, `uzid`; the cover also carried `token`.
No MP4 was obtained and no token was invented or copied between resources.
This verifies cover retrieval and the unavailable-video path, not successful
live video playback. Existing raw-URL resolver limitations above still apply.


## Native playback follow-up

On 2026-10-16 the newly forwarded File Transfer card successfully played in
WeChat's native Channels window (duration 4:18). Therefore the HTTP-400 card URL
is not evidence that the underlying clip is unavailable.

REA 6.5.0 inspected the helper executable (SHA-256
`540adef74c6cfed0bad3906d5460c0dba14def606d86db863183b67aeccf3991`).
Static framework strings identify `finder_js_api.cc`, `finderH5Auth`,
`finderH5ExtTransfer`, `dispatchFinderAction`, and a Finder lite-app entry URL.
They establish a native authenticated bridge exists, but do not establish its
call signatures or return a video URL, URL token, or decode key.

A minimal REA Frida probe of the exact renderer created for this card returned
`capability_unavailable`: unable to access that process from the current user
account. No script ran. The playback helper also declined Computer Use access,
and direct access to its container cache was denied. No signatures, debugging
settings, privacy permissions, or security protections were changed.

Covers now load without clicking, scoped to message IDs and only when a card
approaches the viewport. Video resolution/decryption through the native bridge
remains unimplemented; no local playback success is claimed.

## Renderer attach diagnosis and proposed experiment

A follow-up REA `inspect-signature` inspection found:

- Renderer identity: `com.tencent.flue.helper.renderer`.
- Tencent team: `5A4RE8SF68`; Hardened Runtime enabled.
- Entitlements: `com.apple.security.app-sandbox`, `com.apple.security.cs.allow-jit`,
  `com.apple.security.inherit`; `com.apple.security.get-task-allow` is absent.
- Renderer SHA-256:
  `54e5542ed950e98092407982da638cc44f178910c53b879d925cf8e5edc8a52c`.
- Renderer still runs as the current user, so this was not a different-user PID.
- REA/Frida successfully attached to a freshly compiled disposable C process,
  executed the exact minimal `send({pid:Process.id,...})` probe, and detached
  cleanly. This rules out a general inability of this REA installation to attach.
- REA's Frida adapter maps attach exceptions to `capability_unavailable`; that
  wrapper is not a separate REA permission policy.

These observations strongly point to the target's macOS debugging protections.
The generic Frida attach error does not identify every task-port authorization
check that failed. Full Disk Access and Accessibility are different permissions;
neither alone establishes permission to debug this hardened renderer. The
main app's embedded Gadget instruments only that process, not its child renderer.

Proposed next experiment (requires explicit user approval before application):

1. Make a separate `WeChatChannelsResearch.app` development copy of the prepared
   app, leaving both the stock installation and current configured bridge intact.
2. Preserve the renderer's existing sandbox/JIT/inheritance entitlements and add
   `com.apple.security.get-task-allow` to its development signature. Re-sign the
   changed enclosing bundles as necessary; do not apply system-wide task-port
   policy changes or disable SIP. Launch only one WeChat instance at a time.
3. Verify the development signature and run the minimal REA attach/detach probe
   before loading any playback instrumentation. Task-port approval may still be
   required by macOS. Do not claim success until an attach actually succeeds.
4. If attaching succeeds, correlate instrumentation with only the newly forwarded
   File Transfer card's object ID. Recover the native resolver's authenticated
   media URL and decoding metadata, or the decrypted compressed-media bytes.
   Do not dump account credentials or unrelated feeds.
5. Only then implement and validate transfer/decryption, verify container and
   codec/duration with a media probe, and demonstrate playback in the local viewer.

If separate library-validation changes or embedded Gadget injection are needed,
report that concrete additional requirement before applying it. Enabling debug
access can expose the development process's WeChat session data to an authorized
local debugger, and re-signing can break helper launch or playback. An attach
success would enable further investigation, not guarantee video recovery.

Sources: [Apple Hardened Runtime](https://developer.apple.com/documentation/xcode/configuring-the-hardened-runtime),
[Apple debugging entitlement guidance](https://developer.apple.com/documentation/security/resolving-common-notarization-issues),
[Frida macOS task-port access](https://frida.re/docs/examples/macos/),
[Frida Gadget process scope](https://frida.re/docs/gadget/).

## Approved development-copy experiment and additional approval boundary

The user approved renderer debugging in a separate development copy. Created
`/tmp/WeChatChannelsResearch.app`, preserved renderer sandbox/JIT/inheritance,
added only `get-task-allow`, re-signed enclosing bundles, and verified deep
signatures. The original configured bridge was unchanged. The copy launched
and signed in, but its AppEx playback helper aborted before creating a renderer.

Capturing startup stderr established the actual failure: `dlopen` rejected
`WeChatAppEx Framework` because the mapping process and mapped file had different
Team IDs. The initial crash stack alone was insufficient to identify this; the
loader error is the conclusive diagnostic. Development signing of enclosing
frameworks changes their signing identity, so the unchanged library-validation
policy prevents those helpers from loading the framework.

A proposed app-specific `disable-library-validation` exception covering the
AppEx app and its five helper bundles was rejected by automatic approval review:
it materially expands the previously approved renderer-debugging scope. The
rejected action was not applied. No alternative route was attempted around that
rejection. Exact current entitlements and proposed additions are recorded in
`channels-research-signing-plan.json`, status awaiting user approval. The five
helper bundles are WeApp, GPU, Plugin, Renderer, and generic Helper. Only the
renderer would retain the additional `get-task-allow` entitlement.

Stopped the research API and exited its app; restored the original bridge.
Video recovery is still unimplemented. Additional permission would permit
loading instrumentation in the development playback processes and could expose
their WeChat session memory to local code; it does not guarantee video recovery.


## Successful recovery after the additional approval

The user explicitly approved library-validation exceptions for AppEx and its five
helpers. Applied those exceptions only to the research copy and verified deep
signatures. Direct Frida attach then reached injection, but the renderer was killed
with CODESIGNING / Invalid Page. Signed Gadget loading initially hit that same
runtime restriction. Frida `code_signing: required` provided functioning read-only
access while retaining Hardened Runtime and the existing sandbox entitlements.

The renderer's tiny Mach-O header had no space for the standard long Gadget load
path. The reproducible tool uses `@loader_path/g`, keeps LC_UUID (required by this
macOS dyld), and replaces LC_BUILD_VERSION's build-tool metadata with the equivalent
LC_VERSION_MIN_MACOSX minimum/SDK fields. Code and section addresses remain fixed.

A protected region caused the ordinary memory scan to fault, so the implementation
uses `readVolatile` and skips inaccessible regions. Correlated native performance
metadata exposed a signed URL with the exact message encfilekey, digest and path.
It returned 16,258,283 encrypted bytes. Recovered a decrypted prefix only after the
following 64 bytes matched that exact source file. The reconstructed MP4 validated
structurally, matched the native 4:18 clip, decoded completely with ffmpeg, played
in the local viewer, and downloaded with an identical SHA-256. No decoding key was
recovered or guessed; this implementation reuses WeChat's decrypted media buffer.

The working copy is now managed under the standard CLI data directory. Its original
runtime configuration was preserved before selecting the research copy. Recovered
MP4 data is private and scoped by account, message ID and content. The reproducible
setup tool was independently run against a fresh copy and passed deep verification.

## 2026-10-16: automatic loading and local viewer handoff

Uncached chat cards now open through the configured app's scoped Channels history.
The opener resolves a uniquely named chat and a unique caption/author pair from
native message history, raises the history window, verifies its focus, resets
its retained scroll position, and activates the matching card. Existing drafts
are untouched. Recovery continues to verify the exact encrypted-file key and
path; a digest is required to match only when the card supplies one.

Some cards omit `m` entirely, and Chromium may retain the signed playback URL
as a standalone string rather than a JSON `url` property. Both representations
are now supported. Renderer discovery repeats within the recovery deadline
because AppEx can replace a renderer during navigation. Renderer exit during
cleanup no longer overrides successful recovery or produces an internal error.

Live verification: the previously failing 02:40:41 card played inside the local
browser (768×576 H.264/AAC, 79.435 seconds, readyState 4, playback time advancing).
Download MP4 saved a 5,060,587-byte file matching the recovered bytes. FFmpeg
completed a full decode without errors. No proxy, separate backend, renderer
code patching, or additional signing/security changes were used for this fix.
