# Native Channels page opening investigation

2026-10-16. This is a bounded static investigation of opening the publishing
page in the already running research app. It does not validate a callable
native URL-opening API and did not navigate, restart, or publish anything.

REA 6.5.0 `inspect-macho` inspected
`WeChatChannelsResearch.app/Contents/Resources/wechat.dylib`:

- Evidence: `ev_5a1364b147b762cc4c1b43faac1eaa30bf664150cca39827f90ace7630291f07`.
- SHA-256: `5f5a81c4d8b000dec1c7203738fc4dba75ce752b55874fc1b6b5869f9dfb1027`.
- Selected architecture: arm64, UUID `0C2F01C0-C197-3286-901D-70E1D007A1C0`.
- The reported public exports are `_SetWeixinCallbackFunc` and `_WeChatMain`.
  REA explicitly marks merged symbol coverage incomplete for stripped binaries;
  this does not establish that no private URL-opening function exists.

The complete REA record is retained at
`/tmp/channels-publishing-main-resource-macho-rea.json` for this session.
The first sandboxed inspection failed while compiling REA's process ownership
helper; setting its module cache under `/tmp` reached inspection but process
supervision then encountered `spawn EPERM`. The approved read-only inspection
outside the sandbox succeeded. No configuration or application signature changed.

A separate plain `strings -a` search of the same shipped library found candidate
fragments `OpenUrlInAnyThre`, `OpenHandoffWindo`, `Open url window`,
`OpenWindowIntern`, `CallBrowserComma`, `open_url_with_xw`, `forceSysBrowser`,
`TryCreateWebView`, and `ExecuteJavaScrip`. Adjacent strings appear compacted or
truncated. These are discovery hints, not recovered function names, signatures,
ownership rules, or observed execution. No direct native call should be built
from them alone.

The main investigation subsequently reached the user's already-open publishing
document through its renderer context. That validated session access makes a
new native URL-opening path unnecessary for publishing from the current page.
Opening the page afresh through a native function remains unresolved.


## 2026-10-17: native opening and default login

The current universal payload SHA-256 is
`5f5a81c4d8b000dec1c7203738fc4dba75ce752b55874fc1b6b5869f9dfb1027`
(4.1.15 / 270135). Bounded arm64 code fragments were extracted from its slice
at container offset 184614912; analysis addresses retain their original VM base.

REA evidence `ev_a3592ff91b300275268b4ee8e4c7e484e09eae3b4a94f96620ccbecf7e5f6348`
recovers `OpenUrlInAnyThread` at 0x4b7642c. The more specific Channels URL
wrapper at 0x2595e60 and native request body at 0x2595edc are recorded by
`ev_a46d0147758a94c4c514a1dcf0390353dc160509da5f3f2713b652dd65c143ae`
and `ev_4b7a462249a3a26aac819006d67d397c15d6b07705a040819d690f5bdfff99da`.
The body does not access its receiver argument; it copies the URL into a native
request, then queues the browser task. The specific wrapper did not reliably load a document. The CLI instead uses
OpenUrlInAnyThread at 0x4b7642c, with the genuine native UI service returned by
0x4670f10 (shared-pointer slot 0xa45df28), a null context and source 78.
The libc++ URL buffer is retained inside the deferred native task closure;
otherwise garbage collection can free its bytes before the native copy.
REA evidence `ev_062fdd77391b4990fedcc8899ef8e9a9c89dd71414c1d3dc37275157bcc0426e`
observed the exact fixed composer URL in the downstream native dispatcher.

Live REA invocation evidence
`ev_691ffc77fe2820096dc2b8e1ea110d4281cc0e0f6d208db64e375c625b698b4c`
confirmed that this request returned without a native fault. This alone does
not establish a loaded/authenticated composer or successful publication.

Default saved-account login now hooks the native saved-info continuation
0x11be90c and invokes `OnAutoLoginButtonClicked` at 0x11b7980 once, after the
valid saved state has been copied. REA evidence
`ev_878978da471feedcd56dce29439df49e07a204534615c549ee581f804fc1fe37`
and `ev_24c6ae137a04f9be04fc0dbd5e8598deb0548b927d703871460dc631e70fc187`
records those boundaries. No Accessibility API or synthesized input is used.
Both features require the exact payload digest and native entry signatures.

The embedded OAuth document exposes the official Channels app
`wx20d84d087938fd46`, scope `snsapi_login`, and callback
`https://channels.weixin.qq.com/platform/oauth-callback.html`. Its shipped
JavaScript uses WeChat's own TLS loopback `/api/check-login` and
`/api/authorize` endpoints. The implementation keeps authorization identifiers
and callback codes in that document, restricts the callback origin/path,
and exposes authentication state plus a SHA-256 nonce digest to Python. The
native consent hook arms a single short operation from that verified document.
The server nonce in the native window differs from the loopback request UUID,
so it is not compared to that digest. Only newly constructed native windows
with the exact Channels display name and snsapi_login scope are eligible. It invokes the real affirmative
callback at 0xf4bcd0 once, on the main queue after construction has finished.
The callback builds its response from the native AuthInfo and closes normally;
no authorization response is fabricated.

Cold-start saved-account login has been observed with loginStarted=true and
connected=true. Upload and publication were subsequently verified: the 208,460,041-byte
`decode-tesla.mp4` completed, and `破解特斯拉3D模型` appeared once as the newest
Channels content-list entry at 2026-10-17 22:39. The count increased from 36 to 37.

Two publisher integration bugs were also corrected. The rich-text editor uses
its `updateDescData` event-bus handler to update the publish model; dispatching
a DOM InputEvent alone only changed the displayed caption. The Publish button
DOM can also remain enabled before file upload finishes. Readiness now requires
the native page model's `canPost` and the media file's `status === "done"`, as
well as the existing disabled/dialog checks. Regression fixtures cover both.
The completed upload was reused for publication; no second video was uploaded.
