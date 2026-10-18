# Channels management JavaScript automation evidence

Observed 2026-10-18 using command-line REA and the configured 4.1.15 research app.
No computer-use, accessibility, or synthetic operating-system input was used.

- Runtime list component: `ev_b0b13176689687c7a4b8f4d47b034af389613108952b6849177e160a12c0cd6b`.
- Store schema, pagination and exact IDs: `ev_3337fdc5cfa3d3a3c60392fa99995ed59094d9b033c114bdb74d8a68ea019e86`
  and `ev_dd7b7822689c3053289d7d02b4aee442e389262888f4997ed35a9b01258078a2`.
- Original pagination method: `ev_df4f2a645f17e97215cc73f1c53b681612e905466fa8d14c4c8a062b7a818e6b`.
- Static shipped-bundle analysis: `ev_ed7895333428a22778eeed85f8bc3ed310717550cd167e5bc8ffbcf12875ec13`.
  Selected sources are `499.c64d1604.js`, `755.f508f3b9.js`, and `841.23bdf3af.js`
  from the official `res.wx.qq.com/.../finder-helper-content/res/static/js/` resources
  observed in the live page. Other inventoried sources were outside static scope.
  Static inspection does not itself establish runtime behavior.

The loaded `.post-list` Vue component exposes its `postStore`. Its `list` contains
opaque string IDs, `desc`, timestamps, status, and count fields. The rendered share
count uses `forwardAggregationCount`, rather than `forwardCount`.
`changePageAsync(page)` changes the current page and awaits `getPostList()`.
The store replaces the list on a successful refresh; retaining the same reference
is treated as a failed refresh, preventing stale data from authorizing deletion.

The normal confirmed delete handler calls `postStore.deletePost(objectId)`.
That store method awaits the page's authenticated post service, refreshes its list
on `errCode === 0`, and returns the response. Its underlying request sends only the
resolved `objectId`. The CLI uses that same store action after resolving the ID;
it does not implement an independent Tencent authentication protocol.

Live listing and stats were validated against 36 videos. Deletion behavior is
covered with JavaScript and HTTP fixtures, including exact ID matching, server
rejection, uncertain outcomes, and duplicate prevention. No existing account
video was deleted during implementation.

## Cold-start upload and live pagination verification

The actual `wechat-cli api /api/channels/post` command was invoked after confirming
zero running WeChat main processes. It started the configured app, completed native
saved-account login and Channels quick login, uploaded all 12,068,177 bytes of
`paris.mp4`, and submitted title/caption `Paris`. The refreshed list contained one
matching video (54 seconds, 718×1036, exact file size) and increased from 36 to 37.
All 37 records were retrieved without duplicate IDs in two requests using a
25-item limit and the returned continuation cursor. Page-1 stats were refreshed
separately and explicitly covered only the first 20 videos.

The test also corrected startup waiting for the protected native image, made the
fixed create/list destination explicit over Frida RPC, and treats non-object
results during document navigation as retryable read readiness or explicit
interrupted mutations. These transitions no longer surface untyped exceptions
that obscure whether a video transfer started.

Final live REA verification of the Paris row is recorded by
`ev_ee2aaccec0a627517cfd8a750f7ca78a1465b506fffef25068f1f142b84f2249`: exact
opaque video ID, title/caption Paris, file size 12,068,177, status 1, and total 37.
Final validation: 272 Python tests passed (2 skipped), 32 JavaScript tests passed,
and Ruff checks passed.
