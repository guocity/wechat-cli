"""Opt-in live validation: read messages, cached media and local OCR; never send messages."""

import argparse
import json
from urllib.parse import urlencode

from wechat_cli.client import ApiClient
from wechat_cli.core.config import data_directory
from wechat_cli.core.errors import AppError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--chat-id", default="filehelper")
    args = parser.parse_args()
    client = ApiClient(data_directory(args.data_dir), args.port)
    status = client.request("/api/native/status")
    chats = client.chats()
    history = client.messages(args.chat_id, 100)
    result = {
        "state": status["state"],
        "chatCount": len(chats["items"]),
        "messageCount": len(history["items"]),
        "idsAreStrings": all(isinstance(message["id"], str) for message in history["items"]),
        "textDryRun": client.request(
            "/api/messages/prepare",
            method="POST",
            data={"chatId": args.chat_id, "text": "wechat-cli compatibility dry-run"},
        )["prepared"],
        "media": {},
    }
    for kind in ["image", "voice", "file"]:
        message = next((message for message in history["items"] if message["kind"] == kind), None)
        if not message:
            continue
        query = urlencode({"chatId": args.chat_id, "id": message["id"]})
        try:
            data = client.request("/api/native/media?" + query, binary=True)
            result["media"][kind] = {"bytes": len(data)}
            if kind == "image":
                ocr = client.request(
                    "/api/native/images/text",
                    method="POST",
                    data={"chatId": args.chat_id, "id": message["id"]},
                )
                result["ocr"] = {"status": ocr["status"], "lineCount": len(ocr["lines"])}
        except AppError as error:
            result["media"][kind] = {"error": error.code}
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
