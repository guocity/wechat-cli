# Channels video publishing API (experimental, macOS)

The API drives the signed-in **embedded WeChat browser**. It does not need Chrome,
Playwright, another login, a proxy, or extracted session cookies. WeChat's page
handles upload authentication, video processing, and submission. This is a local
REST wrapper around the page, not an independent Tencent publishing protocol.

Use the explicitly prepared Channels research app described in
[Channels playback](channels-playback.md). This feature reuses its existing signed
helper and authenticated loopback Gadget; it does not change signatures or enable
browser debugging. Stock/ordinary prepared apps return
`channels_native_runtime_required`. Windows does not advertise this capability.

Open exactly one publishing page in that WeChat app:
<https://channels.weixin.qq.com/platform/post/create>.
The observed embedded-browser redirect is `/micro/content/post/create`; both
paths are recognized. Keep the page open and finish any pre-existing draft first.
The API currently requires this manual opening step. No validated native URL
opening function is available.

## Python usage

Run this source checkout's server (`uv run wechat-cli start` in the project), then:

```python
from wechat_cli.client import ApiClient
from wechat_cli.core.config import data_directory

client = ApiClient(data_directory(), timeout=900)
print(client.channel_status())
payload = {
    "videoPath": "/absolute/path/video.mp4",
    "description": "My video description #topic",
    "title": "Short title",
    "waitSeconds": 60,
}

# Upload and prepare for review, without clicking Publish:
draft = client.compose_channel(payload, idempotency_key="channel-upload-001")
print(draft)

# Once status reports ready, explicitly submit the prepared video:
if client.channel_status()["status"] == "ready":
    print(client.publish_channel(draft["sessionId"], idempotency_key="channel-publish-001"))
```

For a single call that uploads and clicks Publish once WeChat enables it:

```python
result = client.compose_channel(payload, idempotency_key="channel-direct-001", post=True)
```

Do not run both examples for the same video. If a direct call returns `processing`,
keep its `sessionId`, poll status, and call `publish_channel` when ready. Repeating
an upload with another key does not resume it; existing drafts are rejected.

## REST interface

All requests use the existing local API Bearer token. Mutations also require
`Idempotency-Key`, with the same persistent 24-hour retry protection as messages.

| Method | Route | Body |
| --- | --- | --- |
| GET | `/api/channels/status` | None |
| POST | `/api/channels/compose` | Exactly one of `videoPath` or `dataBase64`; optional `description`, `title`, `waitSeconds` |
| POST | `/api/channels/post` | Same; additionally requests submission when ready |
| POST | `/api/channels/publish` | `sessionId` from a prepared upload |

Videos must be complete MP4 files. Use `videoPath` with an absolute path on the
server machine for videos **up to 256 MiB**. The server maps the file read-only
and transfers bounded chunks, preserving the original filename. Keep the file
unchanged until the upload finishes. Alternatively, send `dataBase64` for videos
**up to 64 MiB**. Supply exactly one source. WeChat
validates duration/codecs and may reject a file whose container passes local
validation. The embedded page advertises larger vendor limits; they do not
increase this API's limit. Description is at most 4096 characters; short title is
at most 16. `waitSeconds` is 0–120, default 60. Large uploads may need a longer
HTTP client timeout.

Status values are `empty`, `receiving`, `processing`, `ready`, and
`submission_requested`. Results include `sessionId`, `publishEnabled`,
`blockedByDialog`, `receivedBytes`, `path`, `source: "wechat_embedded_browser"`, and
`acknowledgedByServer: false`. A `submission_requested` result means only that
Publish was clicked once. It does **not** confirm publication or supply a public
post ID. Inspect WeChat for moderation, server errors, originality prompts, or
other required interaction. The API never accepts agreements or retries a final
click automatically. After an uncertain result, inspect the page before using
another key. API keys cannot bypass the page's consumed-session guard.

Use the settings API below to configure location, links, activity, scheduling,
originality and video labels. Omitted settings keep their native values. Do not
edit, navigate, or close the publishing page while an upload request runs.

## Validation and remaining gaps

2026-10-16 live read-only validation reached the actual open publishing page and
returned `empty`, `publishEnabled: false`, `blockedByDialog: false`. DOM inspection
verified the video file input, description editor, short-title input, and Publish
button. Publish is disabled by a CSS class, not its HTML `disabled` property; the
adapter checks both. Page selection requires the exact HTTPS origin/path and
rejects multiple matching renderers. Renderer identity, signing policy, task
runner and V8 context signatures reuse playback's checked native boundary.

REA evidence `ev_3767ddeb9c0aa1baf1c52e0868bace264df2a1f9391d42c6eb33370e4c235540`
records the main renderer's read-only isolate/task-runner probe. The complete
session record is `/private/tmp/channels-publisher-renderer-rea.json`.
[Native-opening investigation](channels-publishing-native-open.md) records the
separate static investigation and its unresolved function signature.

The regression suite passed 214 tests with 2 skipped, including strict input
validation, invalid MP4 rejection, upload ordering, processing without submission,
CSS-disabled Publish, one final click, session mismatch, wrong-origin rejection,
and authenticated API idempotency. Live upload was then verified with the user-selected
`~/Downloads/roadster-ui.mp4`: 213,355,649 bytes, 3840×2160 H.264/AAC,
107.81 seconds. The authenticated `/api/channels/compose` request used `videoPath`,
kept description/title blank, and reached `ready` in 269.4 seconds. A subsequent
status read confirmed `receivedBytes: 213355649`, `publishEnabled: true`, and
`blockedByDialog: false`; the native page displayed the matching roadster preview
and generated covers. The upload remains in the open composer, unpublished.
The selected session is `4355fcbe-e405-4e80-a51f-54f3385b8d1a`.
**Publication itself has not been verified**; no final Publish click was made.
Tencent page changes can require selector updates. This feature is experimental.

## All publishing fields

`description` and `title` are top-level fields. Compose/post now also accept an
`options` object. Use the same fields to edit a prepared video through
`POST /api/channels/update` with its `sessionId` and an `Idempotency-Key`.
No re-upload is needed. Omitted/null options keep the current setting.

```python
session = client.channel_status()["sessionId"]

# Inspect exact choices first. Searching changes the native picker, not the post.
places = client.channel_options(
    session, "location", query="Park", idempotency_key="channel-place-search-001"
)
print(places["choices"])  # name, detail, and unique value

# Apply only the requested settings. This call never clicks Publish.
result = client.update_channel(
    session,
    idempotency_key="channel-settings-001",
    description="Roadster interface demo #Roadster",
    title="Roadster UI",
    options={
        "location": "",          # hide location; or use an exact choices[].value
        "link": {"type": "none"},
        "activity": "",          # do not participate
        "scheduleAt": "immediate",
        "declareOriginal": False,
        "videoLabel": "No label needed",
    },
)
print(result["settings"])
```

| Field in `options` | Values |
| --- | --- |
| `location` | Exact returned `value`/unique `name`; `""` means 不显示位置 |
| `locationQuery` | Optional search before choosing the location |
| `link` | `{"type":"article","url":"https://mp.weixin.qq.com/s/…"}`, `{"type":"redEnvelope","url":"https://…"}`, or `{"type":"none"}` |
| `activity` | Exact available `value`/unique `name`; `""` means 不参与活动 |
| `activityQuery` | Optional activity search |
| `scheduleAt` | ISO 8601 with explicit timezone and whole minutes, e.g. `2026-10-17T12:00:00-04:00`; or `"immediate"` |
| `declareOriginal` | Boolean: request the 声明原创 checkbox state |
| `videoLabel` | Exact label from `channel_options(session, "videoLabel", …)`; `""` means No label needed |

Scheduling obeys the native date-picker's account-specific minimum/maximum times;
the API rejects out-of-range timestamps. The account's link choices are read
from the live page: currently this account exposes articles and red-envelope
covers. An arbitrary website URL is not a generic link attachment. The page's
normal URL parsing is used and detected validation failures block submission.

`POST /api/channels/options` accepts `sessionId`, `kind` (`location`, `activity`,
`link`, or `videoLabel`) and optional `query` for location/activity. It requires
an idempotency key because it opens/searches the native picker. Reuse a key only
for the same request; use a new key to refresh dynamic results. Repeated place
names are rejected unless the full returned `value`, including address/detail,
selects exactly one result.

Originality can open the **原创权益** dialog containing **原创声明须知** and **使用条款**.
The API does not accept these agreements. It reports `status: "action_required"`
and `blockedByDialog: true`; review and complete or cancel that dialog in WeChat.
The checked checkbox alone is not proof that its terms step has been completed.
A direct `/post` call stops without clicking Publish when that dialog is present.
After completing it manually, use status, then publish the existing session.

`nativeAction` on `/channels/update` can open `cover`, `topic`, or `mention` for
native editing. Cover cropping, contact selection, and extra details requested
by specific video labels remain interactive in WeChat. Complete/close their
native controls before another API change. No agreement checkbox is exposed as
an automatic action.

Settings responses include current `settings` and link `validation`. Updates are
sequential, not transactional: if a later option fails, earlier field changes
remain in the unpublished draft. Inspect status before retrying. Neither update
nor options ever clicks Publish.

Live settings checks verified reversible description/title edits, article and
red-envelope selector discovery, link clearing, 21 location choices, no-activity
selection, a timezone-aware scheduled timestamp and restoration to immediate,
and opening/cancelling the originality terms dialog. No agreement was accepted
and no post was published. Location changes, valid external link parsing, and
label-specific detail forms are not covered by that live check.
