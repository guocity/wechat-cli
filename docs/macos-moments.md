# macOS Moments validation

Validated against Apple Silicon WeChat 4.1.15, bundle build 270135. The smoke
harness used the real Swift Accessibility helper and signed-in vendor GUI, with
fixture account metadata for the local HTTP service. It did not exercise Frida
posting internals: this feature deliberately uses WeChat's native composer.

## Verified

- Local web media selection and thumbnail previews, including nine generated PNGs.
- One-photo composition with an exact Unicode caption.
- Nine-photo composition through sequential native Add Image operations, with
  nine native Image elements and an exact caption observed in the final composer.
- Native video composition with a video thumbnail and exact caption.
- Native Mention contact selector and all four Visible To choices.
- AI shortcut opening the native Xiaowei assistant. The account's first-use
  authorization prompt was dismissed without accepting terms or submitting text.
- Native discard workflow, including the Save this draft? / Discard confirmation.
- AVFoundation duration inspection accepted 2-second and exactly 30-second H.264
  MP4 fixtures and rejected the 30.04-second fixture.
- Automated schema, invalid media, staging permissions/capacity, platform gate,
  API idempotency, CLI option exclusivity and uncertain Post-session tests.
- Regression suite: 165 passed, 2 existing skips. Ruff checks and JavaScript syntax
  checks passed; the Swift helper compiled with SDK deprecation warnings.

## Publication boundary

No test Moment was posted. The final Post click is covered by the native adapter
and simulated API/session tests; public publication, post IDs and server
acknowledgements are not verified. `submitted` only means the native button was
clicked and the composer closed. Callers must inspect Moments for publication.

The native Emoji button is exposed as an interactive shortcut; emoji selection,
contact/tag selection, AI conversations and native media editing remain inside
WeChat. This is not an independent browser implementation of those pickers.

Native picker operations wait for path-field creation, matching path resolution,
navigation completion and picker closure. Native image counts are checked before
a draft session is returned. An incomplete load reports an error and leaves the
native draft available for manual inspection/discard rather than posting it.
