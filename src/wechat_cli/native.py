"""Bounded subprocess transport. A blocked native call cannot hang the HTTP server."""

import json
import os
import subprocess
import sys
import threading
from concurrent.futures import Future, TimeoutError

from .config import load_runtime
from .errors import AppError
from .models import resolve_chat, resolve_message

SEND_METHODS = {"sendText", "sendVoice"}


class NativeBridge:
    def __init__(self, directory, timeout=20, popen=subprocess.Popen):
        self.directory, self.timeout, self.popen = directory, timeout, popen
        self.lock = threading.RLock()
        self.connect_lock = threading.Lock()
        self.pending = {}
        self.sequence = 0
        self.process = None
        self.ready = None
        self.closed = False
        self.observed = {"connected": False}

    def connect(self):
        with self.connect_lock:
            if self.closed:
                raise AppError("native_bridge_unavailable", "Native bridge is closed", 503)
            if self.process and self.process.poll() is None:
                return
            runtime = load_runtime(self.directory)
            environment = {
                **os.environ,
                "WECHAT_NATIVE_APP": runtime["app"],
                "WECHAT_NATIVE_SUPPORT": runtime["supportLibrary"],
                "WECHAT_DATA_DIR": str(self.directory),
            }
            self.ready = Future()
            try:
                process = self.popen(
                    [sys.executable, "-m", "wechat_cli.worker"],
                    env=environment,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    bufsize=1,
                )
            except OSError:
                raise AppError(
                    "native_bridge_unavailable", "Cannot launch the Python Frida worker", 424
                ) from None
            self.process = process
            threading.Thread(target=self._read, args=(process, self.ready), daemon=True).start()
            try:
                self.ready.result(timeout=max(60, self.timeout))
            except TimeoutError:
                process.terminate()
                raise AppError(
                    "native_bridge_timeout", "WeChat did not start in time; inspect its window and retry", 504
                ) from None

    def _read(self, process, ready):
        try:
            for line in process.stdout:
                value = json.loads(line)
                if value.get("event") == "ready":
                    if not ready.done():
                        ready.set_result(True)
                elif value.get("event") == "failure":
                    if not ready.done():
                        code = value.get("error", "native_bridge_unavailable")
                        ready.set_exception(
                            AppError(
                                code,
                                "Cannot connect to the patched WeChat app. Quit stock WeChat and run doctor.",
                                424,
                            )
                        )
                elif "id" in value:
                    with self.lock:
                        entry = self.pending.pop(value["id"], None)
                    if entry:
                        method, future = entry
                        if "error" in value:
                            code = value["error"]
                            if method in SEND_METHODS and code in {
                                "native_read_failed",
                                "native_send_failed",
                            }:
                                code = "native_send_outcome_unknown"
                            messages = {
                                "native_account_not_ready": "Sign into the configured WeChat app, then retry.",
                                "native_task_context_unavailable": "WeChat is starting or busy. Open its main window and retry.",
                                "native_send_outcome_unknown": "Send outcome is unknown. Inspect WeChat history and reuse the same key for retries.",
                                "native_feature_unavailable": "This feature has not been validated for this WeChat payload.",
                            }
                            future.set_exception(
                                AppError(code, messages.get(code, code.replace("_", " ")), 409)
                            )
                        else:
                            future.set_result(value.get("result"))
        except (OSError, ValueError):
            pass
        finally:
            if not ready.done():
                ready.set_exception(
                    AppError("native_bridge_unavailable", "Frida worker exited during startup", 424)
                )
            with self.lock:
                # Reader of an old process must never fail a replacement process's requests.
                if self.process is process:
                    self.observed["connected"] = False
                    for method, future in self.pending.values():
                        code = (
                            "native_send_outcome_unknown"
                            if method in SEND_METHODS
                            else "native_bridge_unavailable"
                        )
                        future.set_exception(
                            AppError(
                                code, "Worker disconnected. Check WeChat history before retrying a send.", 502
                            )
                        )
                    self.pending.clear()
            process.stdout.close()

    def request(self, method, params=None):
        self.connect()
        future = Future()
        with self.lock:
            if len(self.pending) >= 8:
                raise AppError("native_bridge_busy", "Too many pending native requests", 429)
            self.sequence += 1
            identifier = self.sequence
            self.pending[identifier] = method, future
            try:
                self.process.stdin.write(
                    json.dumps({"id": identifier, "method": method, "params": params or {}}) + "\n"
                )
                self.process.stdin.flush()
            except (OSError, ValueError):
                self.pending.pop(identifier, None)
                code = (
                    "native_send_outcome_unknown" if method in SEND_METHODS else "native_bridge_unavailable"
                )
                raise AppError(code, "Worker disconnected; check WeChat before retrying", 502) from None
        try:
            return future.result(timeout=65 if method == "extractImageText" else self.timeout)
        except TimeoutError:
            with self.lock:
                self.pending.pop(identifier, None)
            code = "native_send_outcome_unknown" if method in SEND_METHODS else "native_read_timeout"
            raise AppError(code, "Native call timed out; check WeChat before retrying", 504) from None

    def status(self):
        value = self.request("status")
        self.observed.update(value)
        return value

    def chats(self):
        value = self.request("listChats")
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
        value = self.request("listMessages", {"chatId": chat_id, "limit": limit})
        items = [resolve_message(item) for item in value["items"]]
        items.sort(
            key=lambda m: (m.get("timestamp", 0), int(m["id"]) if str(m.get("id", "")).isdigit() else 0),
            reverse=True,
        )
        self.observed["historyVerified"] = bool(items) or self.observed.get("historyVerified", False)
        return {**value, "items": items, "order": "newest_first"}

    def send(self, chat_id, text):
        result = self.request("sendText", {"chatId": chat_id, "text": text})
        return receipt(result, chat_id)

    def close(self):
        self.closed = True
        process = self.process
        if process:
            try:
                process.stdin.close()
                process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
        # The worker detaches, leaving WeChat and its login session running.


def receipt(result, chat_id):
    if (
        not isinstance(result, dict)
        or result.get("source") != "native"
        or result.get("receiverVerified") is not True
        or type(result.get("queued")) is not bool
        or not isinstance(result.get("requestId"), str)
    ):
        raise AppError(
            "native_send_outcome_unknown", "Unverified send result; check WeChat history before retrying", 502
        )
    if not result["queued"]:
        raise AppError("native_send_rejected", "WeChat declined to queue the message", 409)
    return {
        "source": "native",
        "chatId": chat_id,
        "requestId": result["requestId"],
        "acceptedByClient": True,
        "acknowledgedByServer": False,
    }
