"""macOS Accessibility operations scoped to the configured WeChat process."""

import hashlib
import json
import os
import re
import subprocess
import tempfile
import threading
import time
import unicodedata
from pathlib import Path

from wechat_cli.core.config import private_directory
from wechat_cli.core.errors import AppError
from wechat_cli.core.media import decode_upload

from .setup import ASSETS, execute, load_runtime


class Controls:
    def __init__(self, directory):
        self.directory = directory
        self.lock = threading.Lock()

    def invoke(self, action, **extra):
        runtime = load_runtime(self.directory)
        source = ASSETS / "controls.swift"
        digest = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
        binary = self.directory / f"native-controls-{digest}"
        if not binary.exists():
            temporary = binary.with_suffix(f".{os.getpid()}.tmp")
            try:
                execute(
                    [
                        "/usr/bin/swiftc",
                        "-module-cache-path",
                        self.directory / "swift-cache",
                        source,
                        "-o",
                        temporary,
                    ]
                )
                os.replace(temporary, binary)
            finally:
                temporary.unlink(missing_ok=True)
        processes = execute(["/bin/ps", "-axo", "pid=,comm="], timeout=5).decode()
        expected = Path(runtime["app"]) / "Contents/MacOS/WeChat"
        matches = matching_processes(processes, expected)
        if len(matches) != 1:
            raise AppError("native_process_identity_mismatch", "Configured WeChat is not running", 409)
        try:
            result = subprocess.run(
                [str(binary)],
                input=json.dumps({"pid": int(matches[0]), "app": runtime["app"], "action": action, **extra}),
                text=True,
                capture_output=True,
                timeout=20,
                check=True,
            )
            value = json.loads(result.stdout)
        except (OSError, ValueError, subprocess.SubprocessError):
            raise AppError(
                "native_control_failed", "Native control failed; inspect WeChat before retrying", 502
            ) from None
        if value.get("error"):
            code = value["error"]
            message = (
                "Allow Accessibility for the terminal running wechat-cli in macOS System Settings."
                if code == "native_accessibility_required"
                else code.replace("_", " ")
            )
            error = AppError(code, message, 409)
            error.possible_send = value.get("possibleSend", True)
            raise error
        return value

    @staticmethod
    def chat_name(native, chat_id):
        chats = native.chats()["items"]
        chat = next((chat for chat in chats if chat["id"] == chat_id), None)
        name = chat.get("displayName") or chat.get("displayString") if chat else None
        if not name or sum((c.get("displayName") or c.get("displayString")) == name for c in chats) != 1:
            raise AppError("native_chat_name_ambiguous", "This operation requires a uniquely named chat", 409)
        return name

    def download_attachment(self, native, chat_id, identifier):
        if not self.lock.acquire(blocking=False):
            raise AppError("native_control_busy", "Another control action is running", 409)
        try:
            name = self.chat_name(native, chat_id)
            from wechat_cli.core.media import find_message

            message = find_message(native, chat_id, identifier)
            if message["kind"] != "file":
                raise AppError("invalid_media_type", "Choose a file attachment")
            state = self.invoke("status")
            if state.get("chatName") != name or state.get("fileUiReady") is False:
                raise AppError(
                    "native_active_chat_mismatch", "Open the requested chat and close other dialogs", 409
                )
            self.invoke("attachment.download", chatName=name, fileName=message["details"]["fileName"])
            for _ in range(40):
                result = native.attachment_data(chat_id, identifier)
                if len(result.get("files", [])) == 1:
                    return {"source": "native", "chatId": chat_id, "id": identifier, "status": "available"}
                self.invoke("attachment.status", fileName=message["details"]["fileName"])
                time.sleep(0.5)
            return {"source": "native", "chatId": chat_id, "id": identifier, "status": "pending"}
        finally:
            self.lock.release()

    def send_file(self, native, input):
        if set(input) != {"chatId", "name", "dataBase64"}:
            raise AppError("invalid_input", "Use chatId, name and dataBase64")
        name = input["name"]
        if (
            not isinstance(name, str)
            or not name.strip()
            or name != name.strip()
            or len(name.encode()) > 200
            or re.search(r"[\\/\x00-\x1f\x7f]", name)
            or name in {".", ".."}
        ):
            raise AppError("invalid_input", "Use a single filename of at most 200 UTF-8 bytes")
        data = decode_upload(input["dataBase64"])
        if not self.lock.acquire(blocking=False):
            raise AppError("native_control_busy", "Another control action is running", 409)
        invoked, directory = False, None
        try:
            chat_id = input["chatId"]
            chat_name = self.chat_name(native, chat_id)
            state = self.invoke("status")
            if state.get("chatName") != chat_name or state.get("fileUiReady") is False:
                raise AppError(
                    "native_active_chat_mismatch", "Open the requested chat and close other dialogs", 409
                )
            if state.get("draft"):
                raise AppError("native_draft_not_empty", "Clear or save the existing WeChat draft first", 409)
            self_id = native.self_id()
            before = {m["id"] for m in native.messages(chat_id, 100)["items"]}
            root = private_directory(self.directory / "outgoing")
            retained = []
            for path in root.glob("file-*"):
                if path.is_dir() and not path.is_symlink():
                    if time.time() - path.stat().st_mtime > 600:
                        import shutil

                        shutil.rmtree(path)
                    else:
                        retained.append(path)
            if len(retained) >= 16:
                raise AppError("native_file_staging_full", "Wait for retained uploads to expire", 429)
            directory = Path(tempfile.mkdtemp(prefix="file-", dir=root))
            path = directory / name
            path.write_bytes(data)
            path.chmod(0o600)
            invoked = True
            try:
                self.invoke("file.send", chatName=chat_name, filePath=str(path), fileName=name)
            except AppError as error:
                if getattr(error, "possible_send", True) is False:
                    invoked = False
                    raise
            deadline, accepted = time.monotonic() + 45, None
            while time.monotonic() < deadline:
                matches = [
                    m
                    for m in native.messages(chat_id, 100)["items"]
                    if m["id"] not in before
                    and m.get("from") == self_id
                    and m.get("to") == chat_id
                    and m.get("kind") == "file"
                    and m["details"].get("sizeBytes") == len(data)
                    and unicodedata.normalize("NFC", m["details"].get("fileName", ""))
                    == unicodedata.normalize("NFC", name)
                ]
                if len(matches) > 1:
                    raise AppError(
                        "native_send_outcome_unknown", "Multiple matching file messages observed", 502
                    )
                if matches:
                    message = matches[0]
                    accepted = {
                        "source": "native",
                        "chatId": chat_id,
                        "requestId": message["id"],
                        "messageId": message["id"],
                        "name": name,
                        "size": len(data),
                        "acceptedByClient": True,
                        "acknowledgedByServer": bool(re.fullmatch(r"[1-9]\d*", message.get("serverId", ""))),
                    }
                    if accepted["acknowledgedByServer"]:
                        return accepted
                time.sleep(0.25)
            if accepted:
                return accepted
            raise AppError(
                "native_send_outcome_unknown", "File selection ran; check WeChat history before retrying", 504
            )
        except Exception as error:
            if invoked and not (isinstance(error, AppError) and error.code == "native_send_outcome_unknown"):
                raise AppError(
                    "native_send_outcome_unknown", "File send outcome is unknown; inspect WeChat", 502
                ) from None
            raise
        finally:
            if directory and not invoked:
                import shutil

                shutil.rmtree(directory)
            self.lock.release()


def matching_processes(snapshot, expected):
    expected = expected.resolve()
    matches = []
    for line in snapshot.splitlines():
        fields = line.strip().split(None, 1)
        if len(fields) == 2 and fields[0].isdigit() and fields[1].endswith("/Contents/MacOS/WeChat"):
            if Path(fields[1]).resolve() == expected:
                matches.append(fields[0])
    return matches
