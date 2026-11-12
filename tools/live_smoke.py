"""Opt-in live validation: read messages, cached media and local OCR; never send messages."""

import argparse
import json
from pathlib import Path
from urllib.parse import urlencode

from wechat_cli.client import ApiClient
from wechat_cli.core.config import data_directory
from wechat_cli.core.errors import AppError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--chat-id", default="filehelper")
    parser.add_argument(
        "--qualify-profile", type=Path, help="Require a signed-in session on this exact profile"
    )
    parser.add_argument("--require-media", choices=("image", "voice", "file"), action="append", default=[])
    args = parser.parse_args()
    if args.require_media and not args.qualify_profile:
        parser.error("--require-media requires --qualify-profile")
    client = ApiClient(data_directory(args.data_dir), args.port)
    status = client.request("/api/native/status")
    profile = None
    if args.qualify_profile:
        profile = json.loads(args.qualify_profile.read_text())
        expected = (profile["version"], profile["build"], profile["payloadSha256"])
        actual = (status.get("wechatVersion"), status.get("build"), status.get("payloadSha256"))
        if actual != expected or status.get("state") != "native_ready" or not status.get("accountAvailable"):
            parser.exit(1, "Start the exact candidate build and sign in before running qualification.\n")
        if status.get("readOnly") != profile.get("readOnly", False):
            parser.exit(1, "The running bridge's read-only setting differs from the profile.\n")
    capabilities = client.request("/api/capabilities")
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
        except AppError as error:
            result["media"][kind] = {"error": error.code}
        else:
            if kind == "image" and capabilities.get("imageTextExtraction"):
                try:
                    ocr = client.request(
                        "/api/native/images/text",
                        method="POST",
                        data={"chatId": args.chat_id, "id": message["id"]},
                    )
                    result["ocr"] = {"status": ocr["status"], "lineCount": len(ocr["lines"])}
                except AppError as error:
                    result["ocr"] = {"error": error.code}
    failures = []
    if profile is not None:
        result["profile"] = {
            "version": status["wechatVersion"],
            "build": status["build"],
            "payloadSha256": status["payloadSha256"],
            "readOnly": status["readOnly"],
        }
        result["capabilities"] = capabilities
        if not (chats["items"] and history["items"] and result["idsAreStrings"] and result["textDryRun"]):
            failures.append("Expected non-empty chats and history, string message IDs, and a passing dry run")
        if profile.get("readOnly") and capabilities.get("sendMessages") is not False:
            failures.append("Read-only profiles must disable sending")
        for kind in sorted(set(args.require_media)):
            if not result["media"].get(kind, {}).get("bytes"):
                failures.append(f"Expected a readable cached {kind} in the selected chat")
        result["failures"] = failures
    print(json.dumps(result, indent=2))
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
