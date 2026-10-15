"""uvx wechat-cli: lifecycle commands and API subcommands with JSON output."""

import argparse
import base64
import json
import sys
import time
import uuid
import webbrowser
from pathlib import Path
from urllib.parse import urlencode

from . import __version__
from .client import ApiClient
from .config import data_directory
from .errors import AppError
from .lifecycle import configured_port, ensure_server, health, serve
from .setup import WECHAT_URL, doctor, setup


def port_number(value):
    try:
        port = int(value)
        if not 1 <= port <= 65535:
            raise ValueError()
        return port
    except ValueError:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535") from None


def parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--data-dir", default=argparse.SUPPRESS, help="Persistent app, token and service directory"
    )
    common.add_argument(
        "--port", type=port_number, default=argparse.SUPPRESS, help="Local API port (default: 8787)"
    )
    root = argparse.ArgumentParser(
        prog="wechat-cli",
        parents=[common],
        description="Python CLI, local API and web interface for macOS WeChat",
    )
    root.add_argument("--version", action="version", version=__version__)
    commands = root.add_subparsers(dest="command")

    def add(name, description):
        return commands.add_parser(name, help=description, description=description, parents=[common])

    init = add("setup", "Download and patch a separate compatible WeChat copy, or register an existing one")
    sources = init.add_mutually_exclusive_group()
    sources.add_argument("--app", help="Register an already patched app")
    sources.add_argument("--source", help="Copy and patch a local stock WeChat.app")
    init.add_argument("--destination", help="New patched app location (must not already exist)")
    init.add_argument("--gadget", help="Use a local Frida Gadget dylib instead of downloading it")
    init.add_argument(
        "--wechat-url", default=WECHAT_URL, help="HTTPS installer URL (payload must be supported)"
    )
    add("doctor", "Check app, payload, Gadget and Frida installation")
    start = add("start", "Start the patched WeChat and persistent local API")
    start.add_argument("--foreground", action="store_true", help="Keep the service in this terminal")
    add("serve", "Run WeChat and the API in the foreground")
    add("stop", "Stop the local API; keep WeChat signed in and running")
    web = add("web", "Start the service and open its web interface")
    web.add_argument("--no-open", action="store_true", help="Print the URL without opening a browser")
    add("status", "Read native WeChat connection status")
    add("chats", "List native conversations")
    messages = add("messages", "Read recent messages, newest first")
    messages.add_argument("chat_id")
    messages.add_argument("--limit", type=int, choices=range(1, 101), default=50, metavar="1..100")
    send = add("send", "Send a text message")
    send.add_argument("chat_id")
    send.add_argument("text")
    send.add_argument(
        "--dry-run", action="store_true", help="Validate native text preparation without sending"
    )
    send.add_argument("--key", help="Reuse this idempotency key for retries (retained for 24 hours)")
    for name in ["send-file", "send-voice"]:
        command = add(
            name, "Send a local file" if name == "send-file" else "Send SILK, mono PCM WAV or raw PCM audio"
        )
        command.add_argument("chat_id")
        command.add_argument("file", type=Path)
        command.add_argument("--key")
        if name == "send-voice":
            command.add_argument("--format", choices=["silk", "wav", "pcm_s16le"], default="wav")
            command.add_argument("--sample-rate", type=int)
    for name, description in [
        ("media", "Download media for a recent message"),
        ("ocr", "Extract text from an image"),
        ("transcribe", "Start WeChat voice-to-text"),
        ("transcript", "Read voice-to-text status"),
        ("download", "Ask the active WeChat chat to download an attachment"),
    ]:
        command = add(name, description)
        command.add_argument("chat_id")
        command.add_argument("message_id", help="Exact string ID from messages")
        if name == "media":
            command.add_argument("--output", required=True, type=Path)
            command.add_argument("--variant", choices=["full", "preview"], default="full")
        if name in {"transcribe", "download"}:
            command.add_argument("--key")
    request = add("api", "Call any local /api/ route")
    request.add_argument("path")
    request.add_argument("--method", choices=["GET", "POST", "DELETE", "PUT", "PATCH"], default="GET")
    request.add_argument("--json", dest="body", help="JSON object body")
    request.add_argument("--key")
    request.add_argument("--output", type=Path, help="Save the response as binary")
    return root


def upload(path):
    if path.stat().st_size > 8 * 1024**2:
        raise AppError("file_too_large", "CLI uploads must be at most 8 MiB", 413)
    return base64.b64encode(path.read_bytes()).decode()


def run(args):
    directory = data_directory(getattr(args, "data_dir", None))
    command = args.command or "start"
    port = configured_port(directory, getattr(args, "port", None))
    if command == "setup":
        if args.app and (args.destination or args.gadget):
            raise AppError("invalid_options", "--destination and --gadget apply to preparing a new copy")
        return setup(
            directory,
            app=args.app,
            source=args.source,
            destination=args.destination,
            gadget=args.gadget,
            wechat_url=args.wechat_url,
        )
    if command == "doctor":
        return doctor(directory)
    if command == "serve" or command == "start" and getattr(args, "foreground", False):
        return serve(directory, port)
    if command == "stop":
        result = ApiClient(directory, port).request("/api/server/stop", method="POST", data={})
        for _ in range(100):
            try:
                health(directory, port)
            except AppError:
                return result
            time.sleep(0.05)
        raise AppError("server_stop_timeout", "Service shutdown is still in progress", 504)
    ensure_server(directory, port)
    if command in {"start", "web"}:
        url = f"http://127.0.0.1:{port}/"
        if command == "web" and not args.no_open:
            webbrowser.open(url)
        return {"running": True, "url": url, "tokenFile": str(directory / "api-token")}
    client = ApiClient(directory, port)
    if command == "status":
        return client.request("/api/native/status")
    if command == "chats":
        return client.chats()
    if command == "messages":
        return client.messages(args.chat_id, args.limit)
    key = getattr(args, "key", None)
    mutation = (
        command in {"send", "send-file", "send-voice", "transcribe", "download"}
        or command == "api"
        and args.method == "POST"
    )
    mutation = mutation and not (command == "send" and args.dry_run)
    if mutation:
        key = key or str(uuid.uuid4())
        print(f"Idempotency-Key: {key} (reuse --key for retries)", file=sys.stderr)
    if command == "send" and args.dry_run:
        return client.request(
            "/api/messages/prepare", method="POST", data={"chatId": args.chat_id, "text": args.text}
        )
    if command == "send":
        return client.send(args.chat_id, args.text, idempotency_key=key)
    if command in {"send-file", "send-voice"}:
        input = {"chatId": args.chat_id, "dataBase64": upload(args.file)}
        if command == "send-file":
            input["name"] = args.file.name
        else:
            input["format"] = args.format
            if args.sample_rate is not None:
                input["sampleRate"] = args.sample_rate
        return client.request(
            "/api/messages/file" if command == "send-file" else "/api/messages/voice",
            method="POST",
            data=input,
            key=key,
        )
    if command in {"media", "ocr", "transcribe", "transcript", "download"}:
        input = {"chatId": args.chat_id, "id": args.message_id}
        paths = {
            "media": "/api/native/media",
            "ocr": "/api/native/images/text",
            "transcribe": "/api/native/voice/transcribe",
            "transcript": "/api/native/voice/transcript",
            "download": "/api/native/attachments/download",
        }
        if command in {"media", "transcript"}:
            if command == "media":
                input["variant"] = args.variant
            result = client.request(paths[command] + "?" + urlencode(input), binary=command == "media")
            if command == "media":
                with args.output.open("xb") as output:
                    output.write(result)
                return {"saved": str(args.output.resolve()), "bytes": len(result)}
            return result
        return client.request(paths[command], method="POST", data=input, key=key)
    if command == "api":
        try:
            input = json.loads(args.body) if args.body is not None else {} if args.method == "POST" else None
            if input is not None and not isinstance(input, dict):
                raise ValueError()
        except ValueError:
            raise AppError("invalid_json", "--json must be a JSON object") from None
        result = client.request(args.path, method=args.method, data=input, key=key, binary=bool(args.output))
        if args.output:
            with args.output.open("xb") as output:
                output.write(result)
            return {"saved": str(args.output.resolve()), "bytes": len(result)}
        return result
    raise AppError("unknown_command", command)


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        result = run(args)
        if result is not None:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except AppError as error:
        print(json.dumps(error.as_dict(), ensure_ascii=False), file=sys.stderr)
        return 1
    except OSError as error:
        print(json.dumps({"error": "filesystem_error", "message": str(error)}), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
