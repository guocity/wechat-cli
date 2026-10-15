"""Opt-in Windows native API validation. Sends only with --send-file-transfer."""

import argparse
import json
import platform
import time
import uuid
from pathlib import Path

from wechat_cli.client import ApiClient
from wechat_cli.core.config import data_directory
from wechat_cli.runtime.lifecycle import ensure_server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--send-file-transfer", action="store_true")
    args = parser.parse_args()
    if platform.system() != "Windows":
        parser.error("Run this probe on Windows")
    directory = data_directory(args.data_dir)
    ensure_server(directory, args.port)
    client = ApiClient(directory, args.port)
    status = client.request("/api/native/status")
    assert status["connected"] is True
    chats = client.chats()
    assert chats["total"] and any(c["id"] == "filehelper" for c in chats["items"])
    messages = client.messages("filehelper", 20)
    assert all(isinstance(m["id"], str) for m in messages["items"])
    prepared = client.request(
        "/api/messages/prepare",
        method="POST",
        data={"chatId": "filehelper", "text": "wechat-cli Windows preparation 微信 📨"},
    )
    assert prepared["prepared"] and not prepared["queued"] and prepared["receiverVerified"]
    report = {
        "version": status["version"],
        "chatCount": chats["total"],
        "historyCount": messages["total"],
        "prepared": True,
        "sent": False,
    }
    if args.send_file_transfer:
        text = "wechat-cli Windows native API test 微信 📨 " + time.strftime("%Y-%m-%d %H:%M:%S")
        key = "windows-live-" + uuid.uuid4().hex
        # One mutation only. Reusing the same key below validates durable deduplication.
        receipt = client.send("filehelper", text, idempotency_key=key)
        assert receipt["acceptedByClient"] and not receipt["acknowledgedByServer"]
        assert client.send("filehelper", text, idempotency_key=key) == receipt
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            history = client.messages("filehelper", 20)
            matches = [m for m in history["items"] if m["content"] == text]
            if matches:
                assert len(matches) == 1 and matches[0]["isOutgoing"]
                report.update(sent=True, outgoingHistoryVerified=True, idempotentRetryVerified=True)
                break
            time.sleep(0.5)
        assert report["sent"], "Send history was not confirmed; inspect WeChat before any further send"
    print(json.dumps(report))


if __name__ == "__main__":
    main()
