import threading
from contextlib import contextmanager

import pytest

from wechat_cli.client import ApiClient
from wechat_cli.models import resolve_message
from wechat_cli.server import ApiServer


class FakeNative:
    def __init__(self):
        self.calls = []
        self.observed = {"connected": True}
        self.closed = False
        self.account = "test-account"
        self.items = [
            resolve_message({"id": "18446744073709551615", "type": 1, "content": "Hello", "timestamp": 1})
        ]

    def request(self, method, params=None):
        self.calls.append((method, params))
        if method == "selfId":
            return {"id": self.account}
        if method == "voiceTranscriptionResult":
            return {"status": "unavailable"}
        if method == "transcribeVoice":
            return {"status": "pending"}
        if method == "extractImageText":
            return {"text": "hello", "lines": ["hello"], "recognition": "wechat", "processing": "local"}
        return {"source": "native", "queued": True, "receiverVerified": True, "requestId": "request-1"}

    def status(self):
        return {"connected": True, "protocol": "wechat-macos"}

    def chats(self):
        self.calls.append(("chats", None))
        return {"items": [{"id": "filehelper", "displayName": "File Transfer"}], "total": 1}

    def messages(self, chat_id, limit):
        self.calls.append(("messages", {"chatId": chat_id, "limit": limit}))
        return {"items": self.items, "total": len(self.items)}

    def send(self, chat_id, text):
        self.calls.append(("send", {"chatId": chat_id, "text": text}))
        return {
            "source": "native",
            "requestId": "test-1",
            "chatId": chat_id,
            "acceptedByClient": True,
            "acknowledgedByServer": False,
        }

    def close(self):
        self.closed = True


@contextmanager
def running_server(directory, native=None):
    native = native or FakeNative()
    server = ApiServer(("127.0.0.1", 0), directory, native=native)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        yield server, ApiClient(directory, server.server_address[1]), native
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.fixture
def api(tmp_path):
    with running_server(tmp_path) as value:
        yield value
