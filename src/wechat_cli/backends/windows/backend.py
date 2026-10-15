"""Windows adapter for native SQLite reads and the native text-send queue."""

import hashlib
import io
import os
import sys
from pathlib import Path

from wechat_cli.core.errors import AppError
from wechat_cli.core.models import receipt, resolve_chat, resolve_message
from wechat_cli.runtime.transport import WorkerTransport

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

    def capabilities(self):
        return {
            "protocol": "wechat-windows",
            "nativeBridge": True,
            "independentClient": False,
            "chatHistory": True,
            "sendMessages": True,
            "mediaPreviews": False,
            "voiceToText": False,
            "imageTextExtraction": False,
            "fileSending": False,
            "voiceSending": False,
        }

    def controls_status(self):
        return {"available": False, "platform": "windows"}

    def _unavailable(self, *args, **kwargs):
        raise AppError(
            "native_feature_unavailable", "This media operation has not been validated on Windows", 424
        )

    send_voice = send_file = attachment_data = voice_data = cached_media = _unavailable
    transcription_result = transcribe_voice = extract_image_text = download_attachment = _unavailable


Backend = WindowsBackend
