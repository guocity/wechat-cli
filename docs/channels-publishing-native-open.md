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
