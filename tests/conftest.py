import socket
import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
import uvicorn

from wechat_cli.api.app import create_app
from wechat_cli.client import ApiClient
from wechat_cli.core.models import resolve_message


class FakeNative:
    name = "fake"
    stop_reason = None

    def connect(self):
        pass

    def is_alive(self):
        return self.alive and not self.closed

    def capabilities(self):
        return {"nativeBridge": True, "chatHistory": True, "sendMessages": True, "voiceSending": True}

    def self_id(self):
        return self.account

    def transcription_result(self, chat_id, identifier):
        return self.request("voiceTranscriptionResult", {"chatId": chat_id, "id": identifier})

    def transcribe_voice(self, chat_id, identifier):
        return self.request("transcribeVoice", {"chatId": chat_id, "id": identifier})

    def prepare_text(self, chat_id, text):
        return self.request("prepareText", {"chatId": chat_id, "text": text})

    def __init__(self):
        self.alive = True
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
def running_server(directory, native=None, *, singleton=False):
    native = native or FakeNative()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    address = sock.getsockname()
    app = create_app(directory, backend=native, singleton=singleton, monitor_interval=0.02)
    runner = uvicorn.Server(
        uvicorn.Config(
            app,
            access_log=False,
            log_config=None,
            log_level="critical",
            proxy_headers=False,
            timeout_graceful_shutdown=1,
        )
    )
    app.state.shutdown = lambda: setattr(runner, "should_exit", True)

    def run():
        try:
            runner.run(sockets=[sock])
        except SystemExit:
            if not app.state.startup_error:
                raise

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not runner.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.005)
    if not runner.started:
        runner.should_exit = True
        thread.join(timeout=3)
        sock.close()
        raise app.state.startup_error or RuntimeError("Test Uvicorn did not start")
    server = SimpleNamespace(
        directory=directory, server_address=address, app=app, runner=runner, thread=thread
    )
    try:
        yield server, ApiClient(directory, address[1]), native
    finally:
        runner.should_exit = True
        thread.join(timeout=5)
        sock.close()
        assert not thread.is_alive(), "Uvicorn did not shut down"


@pytest.fixture
def api(tmp_path):
    with running_server(tmp_path) as value:
        yield value
