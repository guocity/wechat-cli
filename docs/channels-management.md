# Channels video management (experimental, macOS)

Use the configured Channels research runtime and the same local API credentials as publishing. Commands start the local service when needed and print JSON.

```sh
wechat-cli channels videos --limit 20
wechat-cli channels videos --limit 20 --cursor 'OPAQUE_CURSOR_FROM_RESPONSE'
wechat-cli channels stats
wechat-cli channels delete VIDEO_ID --key channels-delete-001
```

Use an exact string ID returned by the video list for deletion. Deletion changes the Channels account; reuse the same key when retrying the same deletion. A reused key with a different video ID is rejected. The CLI generates and prints a key to stderr if `--key` is omitted.

The equivalent local API routes are:

| Route | Input |
| --- | --- |
| `GET /api/channels/videos` | Optional `limit` (integer 1–100, default 20), optional `cursor` (opaque string, 1–4096 characters) |
| `GET /api/channels/stats` | No query parameters |
| `POST /api/channels/delete` | JSON `{"videoId":"VIDEO_ID"}` and `Idempotency-Key` header |

Pagination cursors must be URL encoded when constructing a query manually; the Python client handles this automatically. IDs stay strings, preserving values larger than JavaScript's safe integer range. Inputs reject unknown fields and control characters. Deletion requires an idempotency key of 8–128 letters, digits, underscores, or hyphens.

```python
from wechat_cli.client import ApiClient
from wechat_cli.core.config import data_directory

client = ApiClient(data_directory())
videos = client.list_channel_videos(limit=20)
statistics = client.channel_stats()
# Explicit account mutation:
# client.delete_channel_video("VIDEO_ID", idempotency_key="channels-delete-001")
```

The backend methods are `list_channel_videos(limit=20, cursor=None)`, `channel_stats()`, and `delete_channel_video(video_id)`. On macOS they delegate to `channels_manager.list_videos`, `channels_manager.stats`, and `channels_manager.delete_video` respectively. Responses come directly from that manager; API and CLI do not infer successful deletion or substitute missing statistics.

Video responses include exact opaque `videoId` strings, short title, description,
creation timestamp, raw status/visibility, media dimensions/duration, and the
page's counts for views, likes, comments, aggregate shares, and favorites.
Signed media URLs and authentication data are not returned. `nextCursor` continues
from the next result, including across the webpage's native 20-item pages.

Stats refresh page 1 and return `scope: "loaded_page"`, `loadedVideos`, `pageSize`,
and `totalVideos`. The aggregate counters cover only those loaded videos; they
are not lifetime account totals. Missing counters remain `null`.

Deletion resolves the exact ID against the account's fresh list and invokes the
same `postStore.deletePost(videoId)` action used by the webpage's confirmation
handler. Success requires an actual zero-error server response. Unknown outcomes
are not retried automatically. `verifiedAbsent` records whether a subsequent
list refresh confirmed removal. Implementing deletion does not delete any video;
only the explicit delete command mutates the account.

The management page is opened and authenticated automatically inside WeChat.
Chrome, computer-use tools, and manual browser setup are unnecessary. Research
used command-line REA against the signed native renderer and the page's observed
official JavaScript bundles. See [implementation evidence](channels-management-evidence.md).
