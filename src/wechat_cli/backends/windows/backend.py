"""Windows adapter for native SQLite reads and the native text-send queue."""

import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from wechat_cli.core.errors import AppError
from wechat_cli.core.media import decode_media, decode_upload, find_message
from wechat_cli.core.models import receipt, resolve_chat, resolve_message
from wechat_cli.runtime.transport import WorkerTransport

from .media_cache import MediaCache
from .processes import check_instance
from .setup import doctor as doctor
from .setup import ensure_current as ensure_current
from .setup import setup as setup


def content(value):
    if isinstance(value, dict) and "hex" in value:
        data = bytes.fromhex(value["hex"])
        if data.startswith(b"\x28\xb5\x2f\xfd"):
            if sys.version_info >= (3, 14):
                from compression import zstd

                try:
                    decoder = zstd.ZstdDecompressor()
                    data = decoder.decompress(data, max_length=1024 * 1024 + 1)
                    if not decoder.eof:
                        raise ValueError()
                except (zstd.ZstdError, ValueError):
                    raise AppError(
                        "native_message_decode_failed", "Cannot decode bounded message content", 424
                    ) from None
            else:
                import zstandard

                try:
                    with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(data)) as reader:
                        data = reader.read(1024 * 1024 + 1)
                except zstandard.ZstdError:
                    raise AppError(
                        "native_message_decode_failed", "Cannot decode compressed message content", 424
                    ) from None
            if len(data) > 1024 * 1024:
                raise AppError("native_message_decode_failed", "Decoded message content is too large", 424)
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            raise AppError(
                "native_message_decode_failed", "Message content is not valid UTF-8", 424
            ) from None
    return value or ""


class WindowsBackend:
    name = "windows"

    def __init__(self, directory):
        self.directory, self.transport, self.executable = directory, None, None
        self.observed = {"connected": False}
        self.media_cache = MediaCache(self)
        self.media_lock = threading.RLock()

    def connect(self):
        runtime = ensure_current(self.directory)
        self.executable = Path(runtime["app"]) / "Weixin.exe"
        check_instance(self.executable)
        self.transport = WorkerTransport(
            [sys.executable, "-m", "wechat_cli.backends.windows.worker"],
            {
                **os.environ,
                "WECHAT_NATIVE_APP": runtime["app"],
                "WECHAT_NATIVE_STATE": str(self.directory),
                "PYTHONUTF8": "1",
            },
        )
        self.transport.connect()
        if not check_instance(self.executable, self.transport.pid):
            raise AppError("wechat_closed", "WeChat closed during startup", 503)

    def close(self):
        if self.transport:
            self.transport.close()

    @property
    def stop_reason(self):
        return self.transport.stop_reason if self.transport else None

    def is_alive(self):
        return bool(
            self.transport
            and not self.transport.stopped.is_set()
            and self.transport.process.poll() is None
            and check_instance(self.executable, self.transport.pid)
        )

    def _request(self, method, params=None):
        return self.transport.request(method, params)

    def _query(self, database, sql, parameters=None, maximum=20000):
        return self._request(
            "query", {"database": database, "sql": sql, "parameters": parameters or [], "maximum": maximum}
        )

    def status(self):
        value = self._request("status")
        self.observed.update(value)
        return value

    def self_id(self):
        return self._request("selfId")["id"]

    def chats(self):
        rows = self._query(
            "session.db",
            "SELECT username,unread_count,is_hidden,summary,status,last_timestamp,sort_timestamp FROM SessionTable LIMIT 20000",
        )
        contacts = {
            r["username"]: r
            for r in self._query("contact.db", "SELECT username,remark,nick_name FROM contact LIMIT 20000")
        }
        items = []
        for row in rows:
            identifier = row["username"]
            contact = contacts.get(identifier, {})
            items.append(
                resolve_chat(
                    {
                        "id": identifier,
                        "displayName": contact.get("remark") or contact.get("nick_name") or identifier,
                        "unreadCount": int(row["unread_count"] or 0),
                        "lastMessageTime": int(row["last_timestamp"] or 0),
                        "isHidden": bool(int(row["is_hidden"] or 0)),
                    }
                )
            )
        for item in items:
            if item.get("chatType") == "folder":
                item["folderMembershipKnown"] = False
        items.sort(key=lambda c: c.get("lastMessageTime", 0), reverse=True)
        return {"source": "native", "items": items, "total": len(items), "order": "recent"}

    def messages(self, chat_id, limit=50):
        if not any(c["id"] == chat_id for c in self.chats()["items"]):
            raise AppError("native_chat_not_found", "Conversation was not found", 404)
        table = "Msg_" + hashlib.md5(chat_id.encode("utf-8")).hexdigest()
        account = self.self_id()
        contacts = {
            r["username"]: r
            for r in self._query("contact.db", "SELECT username,remark,nick_name FROM contact LIMIT 20000")
        }
        items = []
        for database in self._request("databases"):
            if not database.startswith(("message_", "biz_message_")) or not database.endswith(".db"):
                continue
            exists = self._query(
                database, "SELECT name FROM sqlite_master WHERE type=? AND name=?", ["table", table], 1
            )
            if not exists:
                continue
            rows = self._query(
                database,
                f'SELECT m.local_id,m.server_id,m.local_type,m.create_time,m.sort_seq,n.user_name AS sender,CAST(m.message_content AS BLOB) AS content FROM "{table}" m LEFT JOIN Name2Id n ON n.rowid=m.real_sender_id ORDER BY m.create_time DESC,m.sort_seq DESC,m.local_id DESC LIMIT ?',
                [limit],
                limit,
            )
            for row in rows:
                sender = row["sender"] or ""
                contact = contacts.get(sender, {})
                kind = int(row["local_type"])
                items.append(
                    (
                        (int(row["create_time"]), int(row["sort_seq"]), int(row["local_id"])),
                        resolve_message(
                            {
                                "id": str(row["local_id"]),
                                "serverId": str(row["server_id"]),
                                "chatId": chat_id,
                                "type": kind & 0xFFFFFFFF,
                                "subType": kind >> 32,
                                "timestamp": int(row["create_time"]),
                                "content": content(row["content"]),
                                "senderId": sender,
                                "senderName": contact.get("remark") or contact.get("nick_name") or sender,
                                "isOutgoing": sender == account,
                            }
                        ),
                    )
                )
        # Local IDs are shard-local; retain WeChat's sort sequence when merging.
        items.sort(key=lambda item: item[0], reverse=True)
        return {
            "source": "native",
            "items": [message for _, message in items[:limit]],
            "total": min(limit, len(items)),
            "order": "newest_first",
        }

    def prepare_text(self, chat_id, text):
        return self._request("prepareText", {"chatId": chat_id, "text": text})

    def send(self, chat_id, text):
        return receipt(self._request("sendText", {"chatId": chat_id, "text": text}), chat_id)

    def native_tool(self, action, data, rate=24000):
        with self.media_lock, tempfile.TemporaryDirectory(prefix="media-", dir=self.directory) as temporary:
            source, destination = Path(temporary) / "input", Path(temporary) / "output"
            source.write_bytes(data)
            try:
                subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "wechat_cli.backends.windows.native_tools",
                        str(self.executable.parent),
                        action,
                        str(source),
                        str(destination),
                        str(rate),
                    ],
                    check=True,
                    timeout=15,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                with destination.open("rb") as stream:
                    result = stream.read(32 * 1024**2 + 1)
                if not result or len(result) > 32 * 1024**2:
                    raise ValueError()
                return result
            except (OSError, ValueError, subprocess.SubprocessError):
                raise AppError(
                    "native_media_decode_failed", "WeChat's local media codec failed", 422
                ) from None

    def convert_audio(self, action, data, rate=24000):
        return self.native_tool(action, data, rate)

    def cached_media(self, value):
        return self.media_cache.cached(value)

    def attachment_data(self, chat_id, identifier):
        message = find_message(self, chat_id, identifier)
        if message["kind"] != "file":
            raise AppError("invalid_media_type", "Choose a file message")
        return self.media_cache.attachment(message)

    def voice_bytes(self, message):
        matches = []
        for database in self._request("databases"):
            if not database.startswith("media_") or not database.endswith(".db"):
                continue
            rows = self._query(
                database,
                "SELECT v.voice_data FROM VoiceInfo v JOIN Name2Id n ON n.rowid=v.chat_name_id "
                "WHERE n.user_name=? AND v.local_id=? AND (v.svr_id=? OR v.svr_id=0) AND v.create_time=? LIMIT 2",
                [message["chatId"], message["id"], message["serverId"], message["timestamp"]],
                2,
            )
            matches.extend(bytes.fromhex(row["voice_data"]["hex"]) for row in rows)
        matches = list(set(matches))
        if len(matches) > 1:
            raise AppError("native_media_ambiguous", "Multiple voice payloads match this message", 409)
        if not matches:
            raise AppError("native_media_not_cached", "Play the voice message in WeChat first", 404)
        data = decode_media(matches[0])
        if not data:
            raise AppError("native_media_decode_failed", "Unsupported native voice payload", 422)
        return data

    def voice_data(self, chat_id, identifier):
        message = find_message(self, chat_id, identifier)
        if message["kind"] != "voice":
            raise AppError("invalid_media_type", "Choose a voice message")
        data = self.voice_bytes(message)
        # Shared media lookup uses this digest only as an opaque scoped cache key.
        digest = hashlib.md5(data).hexdigest()
        return {"items": [{"id": identifier, "nativeMediaFields": {"voiceFile": digest}}]}

    @staticmethod
    def image_dimensions(data):
        from .images import dimensions

        return dimensions(data)

    def extract_image_text(self, data):
        from .processes import owned_helper_parent

        binary = decode_upload(data)
        with self.media_lock, tempfile.TemporaryDirectory(prefix="ocr-", dir=self.directory) as temporary:
            source = Path(temporary) / "input.png"
            source.write_bytes(binary)
            command = [
                sys.executable,
                "-m",
                "wechat_cli.backends.windows.ocr",
                str(self.executable.parent),
                str(source),
                str(Path(temporary) / "helper-ready.json"),
            ]
            process = None
            try:
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                # A Windows venv executable can be a launcher. The actual
                # interpreter announces its PID before it may launch wxocr.
                ready = Path(temporary) / "helper-ready.json"
                deadline = time.monotonic() + 5
                while not ready.exists():
                    if process.poll() is not None or time.monotonic() >= deadline:
                        raise AppError("native_ocr_start_failed", "OCR helper did not become ready", 424)
                    time.sleep(0.01)
                parent = json.loads(ready.read_text(encoding="utf-8"))["pid"]
                if type(parent) is not int or not 0 < parent <= 0xFFFFFFFF:
                    raise ValueError()
                with owned_helper_parent(parent, self.executable):
                    try:
                        output, _ = process.communicate(input=b"start\n", timeout=25)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.communicate()
                        raise AppError("native_ocr_timeout", "WeChat OCR timed out", 504) from None
                result = json.loads(output)
                if process.returncode or "error" in result:
                    raise AppError(
                        result.get("error", "native_ocr_failed"),
                        "WeChat OCR could not recognize the image",
                        424,
                    )
                return result
            except (OSError, ValueError):
                raise AppError("native_ocr_failed", "Cannot run WeChat's local OCR helper", 424) from None
            finally:
                if process is not None and process.poll() is None:
                    process.kill()
                    process.communicate()

    def stage_outgoing(self, name, data):
        import re
        import shutil
        import time

        from wechat_cli.core.config import private_directory

        if (
            not isinstance(name, str)
            or not name.strip()
            or name != name.strip()
            or len(name.encode("utf-8")) > 200
            or re.search(r'[<>:"/\\|?*\x00-\x1f\x7f]', name)
            or name.endswith((".", " "))
            or name.split(".")[0].upper()
            in {
                "CON",
                "PRN",
                "AUX",
                "NUL",
                *(f"COM{i}" for i in range(1, 10)),
                *(f"LPT{i}" for i in range(1, 10)),
            }
        ):
            raise AppError("invalid_input", "Supply a plain Windows file name")
        root = private_directory(self.directory / "outgoing")
        retained = []
        for path in root.glob("send-*"):
            if path.is_dir() and not path.is_symlink():
                if time.time() - path.stat().st_mtime > 600:
                    shutil.rmtree(path)
                else:
                    retained.append(path)
        if len(retained) >= 16:
            raise AppError("native_file_staging_full", "Wait for retained uploads to expire", 429)
        folder = private_directory(Path(tempfile.mkdtemp(prefix="send-", dir=root)))
        path = folder / name
        path.write_bytes(data)
        return path

    def send_file(self, payload):
        data = decode_upload(payload["dataBase64"])
        with self.media_lock:
            path = self.stage_outgoing(payload["name"], data)
            return receipt(
                self._request(
                    "sendFile",
                    {
                        "chatId": payload["chatId"],
                        "path": str(path),
                        "name": payload["name"],
                        "size": len(data),
                    },
                ),
                payload["chatId"],
            )

    def capabilities(self):
        return {
            "protocol": "wechat-windows",
            "nativeBridge": True,
            "independentClient": False,
            "chatHistory": True,
            "sendMessages": True,
            "mediaPreviews": True,
            "voiceToText": "wechat_native_task",
            "imageTextExtraction": "wechat_local_ocr",
            "fileSending": "wechat_native_queue",
            "voiceSending": "wechat_native_queue",
        }

    def controls_status(self):
        return {"available": False, "platform": "windows"}

    def send_voice(self, prepared):
        return self._request("sendVoice", prepared)

    def voice_record(self, chat_id, identifier):
        message = find_message(self, chat_id, identifier)
        if message["kind"] != "voice":
            raise AppError("invalid_media_type", "Choose a voice message")
        table = "Msg_" + hashlib.md5(chat_id.encode()).hexdigest()
        rows = []
        for database in self._request("databases"):
            if not database.startswith(("message_", "biz_message_")) or not database.endswith(".db"):
                continue
            if not self._query(
                database, "SELECT name FROM sqlite_master WHERE type=? AND name=?", ["table", table], 1
            ):
                continue
            rows.extend(
                self._query(
                    database,
                    f"SELECT local_id,server_id,sort_seq,create_time,CAST(message_content AS BLOB) AS content,"
                    f'CAST(source AS BLOB) AS source,packed_info_data FROM "{table}" WHERE local_id=? '
                    "AND create_time=? AND local_type=? LIMIT 2",
                    [identifier, message["timestamp"], 34],
                    2,
                )
            )
        if len(rows) != 1:
            raise AppError(
                "native_message_ambiguous" if rows else "native_message_not_found",
                "Cannot identify this voice message uniquely",
                409,
            )
        row = rows[0]
        packed = row["packed_info_data"]
        return {
            "account": self.self_id(),
            "chatId": chat_id,
            "id": str(row["local_id"]),
            "serverId": str(row["server_id"]),
            "sortSequence": str(row["sort_seq"]),
            "timestamp": int(row["create_time"]),
            "content": content(row["content"]),
            "source": content(row["source"]),
            "packed": list(bytes.fromhex(packed["hex"])) if packed else [],
            "sender": message.get("senderId", ""),
            "outgoing": message.get("isOutgoing", False),
        }

    def transcription_result(self, chat_id, identifier):
        return self._request("voiceTranscript", self.voice_record(chat_id, identifier))

    def transcribe_voice(self, chat_id, identifier):
        return self._request("transcribeVoice", self.voice_record(chat_id, identifier))

    def download_attachment(self, chat_id, identifier):
        message = find_message(self, chat_id, identifier)
        if message["kind"] != "file":
            raise AppError("invalid_media_type", "Choose an attachment message")
        files = self.media_cache.attachment(message)["files"]
        if not files:
            raise AppError("native_media_not_cached", "Download this attachment in WeChat first", 404)
        return {"source": "native", "chatId": chat_id, "id": identifier, "status": "completed"}


Backend = WindowsBackend
