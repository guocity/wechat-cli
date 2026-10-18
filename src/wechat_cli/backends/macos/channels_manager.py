"""Manage the authenticated Channels list through its signed native page runtime."""

import time

from wechat_cli.core.errors import AppError

from .channels_publisher import upload_page
from .setup import load_runtime


def _operate(backend, command):
    if not backend.channels_lock.acquire(blocking=False):
        raise AppError("native_control_busy", "Another Channels operation is running", 409)
    try:
        with upload_page(backend, load_runtime(backend.directory)["app"], target="manager") as call:
            result = call(command)
            deadline = time.monotonic() + 90
            while result.get("status") == "pending":
                if time.monotonic() >= deadline:
                    raise AppError(
                        "channels_operation_interrupted",
                        "Channels operation is still pending; inspect before retrying",
                        504,
                    )
                time.sleep(0.25)
                result = call({"action": "job", "operationId": result["operationId"]})
            return result
    finally:
        backend.channels_lock.release()


def list_videos(backend, *, limit=20, cursor=None):
    return _operate(backend, {"action": "list", "limit": limit, "cursor": cursor})


def stats(backend):
    return _operate(backend, {"action": "stats"})


def delete_video(backend, video_id):
    return _operate(backend, {"action": "delete", "videoId": video_id})
