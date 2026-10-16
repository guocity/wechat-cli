"""macOS adapter: normalize native results and hide RPC/UI details from shared code."""

import hashlib
import json
import os
import sys
import threading
import time
from pathlib import Path

from wechat_cli.core.models import receipt, resolve_chat, resolve_message
from wechat_cli.runtime.transport import WorkerTransport

from .controls import Controls
from .processes import check_instance
from .setup import doctor as doctor
from .setup import setup as setup
from .upgrade import ensure_current as ensure_current


class MacOSBackend:
    name = "macos"

    def __init__(self, directory):
        self.directory = directory
        self.controls = Controls(directory)
        self.transport = None
        self.observed = {"connected": False}
        self.executable = None
        self.channels_lock = threading.Lock()

    def connect(self):
        runtime = ensure_current(self.directory)
        self.executable = Path(runtime["app"]) / "Contents/MacOS/WeChat"
        check_instance(self.executable)
        self.transport = WorkerTransport(
            [sys.executable, "-m", "wechat_cli.backends.macos.worker"],
            {
                **os.environ,
                "WECHAT_NATIVE_APP": runtime["app"],
                "WECHAT_NATIVE_SUPPORT": runtime["supportLibrary"],
                "WECHAT_DATA_DIR": str(self.directory),
            },
        )
        self.transport.connect()
        if not check_instance(self.executable, self.transport.pid):
            from wechat_cli.core.errors import AppError

            raise AppError("wechat_closed", "WeChat closed during startup", 503)
        self.auto_login()

    def auto_login(self):
        if self.status().get("accountAvailable"):
            return
        result = self.controls.invoke("login.saved")
        if not result.get("loginStarted"):
            return
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self.status().get("accountAvailable"):
                return
            time.sleep(0.25)
        # Keep the service available for QR/phone confirmation when required.

    def is_alive(self):
        return bool(
            self.transport
            and not self.transport.stopped.is_set()
            and self.transport.process.poll() is None
            and check_instance(self.executable, self.transport.pid)
        )

    @property
    def stop_reason(self):
        return self.transport.stop_reason if self.transport else None

    def close(self):
        if self.transport:
            self.transport.close()

    def _request(self, method, params=None):
        return self.transport.request(method, params)

    def status(self):
        value = self._request("status")
        self.observed.update(value)
        return value

    def chats(self):
        value = self._request("listChats")
        items = [resolve_chat(item) for item in value["items"]]
        for folder in items:
            if folder.get("folderMembershipKnown"):
                children = [c for c in items if c.get("parentFolderId") == folder["id"]]
                folder["childCount"] = len(children)
                folder["lastMessageTime"] = max(
                    [folder.get("lastMessageTime", 0)] + [c.get("lastMessageTime", 0) for c in children]
                )
        items.sort(key=lambda c: (bool(c.get("isPinned")), c.get("lastMessageTime", 0)), reverse=True)
        self.observed["enumerationObserved"] = True
        return {**value, "items": items, "total": len(items), "order": "pinned_then_recent"}

    def messages(self, chat_id, limit=50):
        value = self._request("listMessages", {"chatId": chat_id, "limit": limit})
        items = [resolve_message(item) for item in value["items"]]
        items.sort(
            key=lambda m: (m.get("timestamp", 0), int(m["id"]) if str(m.get("id", "")).isdigit() else 0),
            reverse=True,
        )
        self.observed["historyVerified"] = bool(items) or self.observed.get("historyVerified", False)
        return {**value, "items": items, "order": "newest_first"}

    def send(self, chat_id, text):
        result = self._request("sendText", {"chatId": chat_id, "text": text})
        return receipt(result, chat_id)

    def self_id(self):
        return self._request("selfId").get("id")

    def prepare_text(self, chat_id, text):
        return self._request("prepareText", {"chatId": chat_id, "text": text})

    def send_voice(self, prepared):
        return self._request("sendVoice", prepared)

    def attachment_data(self, chat_id, identifier):
        return self._request("attachmentData", {"chatId": chat_id, "id": identifier})

    def voice_data(self, chat_id, identifier):
        return self._request("voiceData", {"chatId": chat_id, "id": identifier})

    def channels_video(self, message):
        from wechat_cli.core.config import private_directory, save_json
        from wechat_cli.core.errors import AppError
        from wechat_cli.core.media import channels_filename, complete_mp4, read_channels
        from wechat_cli.core.models import channels_descriptor

        from .channels_native import download
        from .setup import load_runtime

        descriptor = channels_descriptor(message)
        filename = channels_filename(None, descriptor.get("objectId") or message["id"])
        identity = self.self_id()
        if not identity:
            raise AppError("native_account_not_ready", "No active WeChat account", 409)
        digest = hashlib.sha256(
            (identity + "\0" + message["id"] + "\0" + message.get("content", "")).encode()
        ).hexdigest()
        with self.channels_lock:
            directory = private_directory(self.directory / "channels-cache")
            cached = directory / (digest + ".mp4")
            if cached.is_file() and 0 < cached.stat().st_size <= 128 * 1024**2:
                data = cached.read_bytes()
                if complete_mp4(data):
                    try:
                        filename = channels_filename(
                            json.loads(cached.with_suffix(".json").read_text())["filename"], filename
                        )
                    except (OSError, ValueError, KeyError, TypeError):
                        pass
                    return data, "video/mp4", filename
            if descriptor.get("objectId") and descriptor.get("objectNonceId"):
                data, name = download(load_runtime(self.directory)["app"], message)
                mime = "video/mp4"
                name = channels_filename(name or descriptor.get("fileName"), descriptor["objectId"])
            else:
                data, mime, name = read_channels(message, "full")
            temporary = cached.with_suffix(".tmp")
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                with os.fdopen(fd, "wb") as output:
                    output.write(data)
                os.replace(temporary, cached)
                save_json(cached.with_suffix(".json"), {"filename": name or filename})
            finally:
                temporary.unlink(missing_ok=True)
            return data, mime, name or filename

    def cached_media(self, value):
        return self._request("cachedMedia", {"hash": value})

    def transcription_result(self, chat_id, identifier):
        return self._request("voiceTranscriptionResult", {"chatId": chat_id, "id": identifier})

    def transcribe_voice(self, chat_id, identifier):
        return self._request("transcribeVoice", {"chatId": chat_id, "id": identifier})

    def extract_image_text(self, data):
        return self._request("extractImageText", {"dataBase64": data})

    def controls_status(self):
        return self.controls.invoke("status")

    def send_file(self, payload):
        return self.controls.send_file(self, payload)

    def post_moment(self, payload):
        from .moments import post_moment

        return post_moment(self.controls, payload)

    def moments(self, limit=20):
        from wechat_cli.core.errors import AppError

        if not self.controls.lock.acquire(blocking=False):
            raise AppError("native_control_busy", "Another control action is running", 409)
        try:
            return self.controls.invoke("moments.list", limit=limit)
        finally:
            self.controls.lock.release()

    def compose_moment(self, payload):
        from .moments import prepare_moment

        return prepare_moment(self.controls, payload)

    def moment_action(self, session_id, action):
        from .moments import moment_action

        return moment_action(self.controls, session_id, action)

    def download_attachment(self, chat_id, identifier):
        return self.controls.download_attachment(self, chat_id, identifier)

    def capabilities(self):
        return {
            "protocol": "wechat-macos",
            "nativeBridge": True,
            "independentClient": False,
            "chatHistory": True,
            "sendMessages": True,
            "mediaPreviews": True,
            "voiceToText": "experimental_native_task",
            "imageTextExtraction": "wechat_local_ocr",
            "fileSending": "wechat_native_ui",
            "voiceSending": "wechat_native_queue",
            "momentsPosting": "wechat_native_ui",
            "momentsReading": "wechat_native_ui",
        }


Backend = MacOSBackend
