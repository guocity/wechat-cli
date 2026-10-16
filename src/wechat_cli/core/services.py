"""Shared workflows; native RPC and operating-system details stay in backends."""

import base64
import re

from .contracts import WeChatBackend
from .errors import AppError
from .idempotency import Idempotency
from .media import find_message, read_media, validate_image


class WeChatService:
    def __init__(self, directory, backend: WeChatBackend):
        self.backend = backend
        self.idempotency = Idempotency(directory)

    def close(self):
        try:
            self.backend.close()
        finally:
            self.idempotency.close()

    def once(self, operation, key, payload, action):
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", key):
            raise AppError(
                "idempotency_key_required",
                "Supply Idempotency-Key with 8–128 letters, digits, underscores or hyphens",
            )
        account = self.backend.self_id()
        if not isinstance(account, str) or not account:
            raise AppError("native_account_not_ready", "No active WeChat account", 409)
        return self.idempotency.once(account + ":" + operation, key, payload, action)

    def prepare_text(self, chat, text):
        result = self.backend.prepare_text(chat, text)
        if (
            result.get("prepared") is not True
            or result.get("queued") is not False
            or result.get("receiverVerified") is not True
        ):
            raise AppError("native_invalid_response", "Text preparation could not be verified", 502)
        return result

    def ocr(self, chat, identifier):
        message = find_message(self.backend, chat, identifier)
        if message["kind"] != "image":
            raise AppError("invalid_media_type", "Choose an image message")
        data, _, _ = read_media(self.backend, chat, identifier)
        dimensions = (
            self.backend.image_dimensions(data)
            if hasattr(self.backend, "image_dimensions")
            else validate_image(data)
        )
        result = self.backend.extract_image_text(base64.b64encode(data).decode())
        if (
            result.get("recognition") != "wechat"
            or result.get("processing") != "local"
            or not isinstance(result.get("text"), str)
            or len(result["text"]) > 1048576
            or not isinstance(result.get("lines"), list)
            or len(result["lines"]) > 10000
            or any(not isinstance(line, str) or len(line) > 65536 for line in result["lines"])
        ):
            raise AppError("native_invalid_response", "Invalid OCR result", 502)
        return {
            "source": "native",
            "chatId": chat,
            "id": identifier,
            **result,
            **dimensions,
            "status": "completed",
        }


def transcript(native, chat, identifier, trigger=False):
    message = find_message(native, chat, identifier)
    if message["kind"] != "voice":
        raise AppError("invalid_media_type", "Choose a voice message")
    params = {"chatId": chat, "id": identifier}
    value = native.transcription_result(chat, identifier)
    if value.get("status") == "unavailable":
        text = message["details"].get("transcript")
        if text and text.strip():
            return {
                "source": "native",
                **params,
                "status": "completed",
                "text": text,
                "recognition": "wechat",
                "resultSource": "local_message_record",
                "conversionTriggered": False,
            }
    if trigger and value.get("status") in {"unavailable", "failed", "timed_out"}:
        value = native.transcribe_voice(chat, identifier)
    if value.get("status") not in {"pending", "completed", "failed", "timed_out", "unavailable"}:
        raise AppError("native_invalid_response", "Invalid transcription response", 502)
    if value["status"] == "completed" and (
        not isinstance(value.get("text"), str) or not value["text"].strip()
    ):
        raise AppError("native_invalid_response", "Empty completed transcription", 502)
    return {
        "source": "native",
        **params,
        **value,
        "recognition": "wechat",
        "resultSource": "native_task",
        "conversionTriggered": value["status"] != "unavailable",
    }
