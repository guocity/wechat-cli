"""Authenticated loopback HTTP API. All CLI commands use the same service as the web UI."""

import base64
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlsplit

from .config import ASSETS, api_token
from .controls import Controls
from .errors import AppError
from .idempotency import Idempotency
from .media import find_message, prepare_voice, read_media, validate_image
from .native import NativeBridge, receipt


def chat_id(value):
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 1024
        or re.search(r"[\x00-\x1f\x7f]", value)
        or value in {"@placeholder_foldgroup", "brandsessionholder", "brandservicesessionholder"}
    ):
        raise AppError("invalid_input", "A valid chatId is required")
    return value


def message_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{1,20}", value, re.ASCII):
        raise AppError("invalid_input", "Use an exact string message ID")
    return value


def fields(input, allowed, required=None):
    if set(input) - set(allowed) or set(required if required is not None else allowed) - set(input):
        raise AppError("invalid_input", "Use " + ", ".join(allowed))


def transcript(native, chat, identifier, trigger=False):
    message = find_message(native, chat, identifier)
    if message["kind"] != "voice":
        raise AppError("invalid_media_type", "Choose a voice message")
    params = {"chatId": chat, "id": identifier}
    value = native.request("voiceTranscriptionResult", params)
    if value.get("status") == "unavailable":
        text = message["details"].get("transcript")
        if text and text.strip():
            return {
                "source": "native",
                **params,
                "status": "completed",
                "text": text,
                "recognition": "wechat",
                "resultSource": "local_message_record",
                "conversionTriggered": False,
            }
        if trigger:
            value = native.request("transcribeVoice", params)
    if value.get("status") not in {"pending", "completed", "failed", "timed_out", "unavailable"}:
        raise AppError("native_invalid_response", "Invalid transcription response", 502)
    if value["status"] == "completed" and (
        not isinstance(value.get("text"), str) or not value["text"].strip()
    ):
        raise AppError("native_invalid_response", "Empty completed transcription", 502)
    return {
        "source": "native",
        **params,
        **value,
        "recognition": "wechat",
        "resultSource": "native_task",
        "conversionTriggered": value["status"] != "unavailable",
    }


ROUTES = {
    "/api/health": ["GET"],
    "/api/capabilities": ["GET"],
    "/api/openapi.json": ["GET"],
    "/api/native/status": ["GET"],
    "/api/login": ["GET"],
    "/api/native/chats": ["GET"],
    "/api/chats": ["GET"],
    "/api/native/messages": ["GET"],
    "/api/messages": ["GET", "POST"],
    "/api/messages/send": ["POST"],
    "/api/messages/prepare": ["POST"],
    "/api/messages/file": ["POST"],
    "/api/messages/voice": ["POST"],
    "/api/native/media": ["GET"],
    "/api/native/voice/transcript": ["GET"],
    "/api/native/voice/transcribe": ["POST"],
    "/api/native/images/text": ["POST"],
    "/api/native/attachments/download": ["POST"],
    "/api/native/controls": ["GET"],
    "/api/server/stop": ["POST"],
}


class ApiServer(ThreadingHTTPServer):
    daemon_threads = False
    block_on_close = True
    allow_reuse_address = True

    def __init__(self, address, directory, native=None, controls=None):
        self.directory = directory
        self.token = api_token(directory)
        self.native = native or NativeBridge(directory)
        self.controls = controls or Controls(directory)
        self.idempotency = Idempotency(directory)
        self.browser_secret = secrets.token_hex(32)
        self.browser_expires = 0
        self.instance = secrets.token_hex(16)
        super().__init__(address, Handler)

    def server_close(self):
        super().server_close()
        self.native.close()
        self.idempotency.close()


class Handler(BaseHTTPRequestHandler):
    server_version = "wechat-cli"
    sys_version = ""
    protocol_version = "HTTP/1.0"

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, *args):
        pass  # Requests contain private chat identifiers; never write access logs.

    def send(self, value, status=200, mime="application/json; charset=utf-8", extra=None):
        data = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; media-src 'self' blob:; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        )
        for key, val in (extra or {}).items():
            self.send_header(key, val)
        self.end_headers()
        self.wfile.write(data)

    def same_origin(self):
        return self.headers.get("Origin") == f"http://{self.headers.get('Host')}"

    def body(self, maximum=65536):
        if self.headers.get_content_type() != "application/json":
            raise AppError("invalid_content_type", "Use application/json", 415)
        raw = self.headers.get("Content-Length", "")
        if self.headers.get("Transfer-Encoding") or not re.fullmatch(r"\d{1,9}", raw):
            raise AppError("invalid_content_length", "Supply a bounded Content-Length", 411)
        length = int(raw)
        if length > maximum:
            raise AppError("body_too_large", "Request body exceeds the upload limit", 413)
        data = self.rfile.read(length)
        if len(data) != length:
            raise AppError("invalid_json", "Incomplete request body")
        try:
            value = json.loads(data)
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except (ValueError, UnicodeError):
            raise AppError("invalid_json", "Body must be a JSON object") from None

    def query(self, query, allowed):
        if set(query) - set(allowed) or any(len(v) != 1 for v in query.values()):
            raise AppError("invalid_query", "Unknown or duplicate query parameter")
        return {key: values[0] for key, values in query.items()}

    def once(self, path, input, action):
        key = self.headers.get("Idempotency-Key", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", key):
            raise AppError(
                "idempotency_key_required",
                "Supply Idempotency-Key with 8–128 letters, digits, underscores or hyphens",
            )
        # Bind keys to account as well as operation so a login change cannot reuse receipts.
        account = self.server.native.request("selfId").get("id")
        if not isinstance(account, str) or not account:
            raise AppError("native_account_not_ready", "No active WeChat account", 409)
        canonical = "/api/messages/send" if path == "/api/messages" else path
        return self.server.idempotency.once(account + ":" + canonical, key, input, action)

    def handle_api(self):
        if len(self.path) > 4096 or not self.path.startswith("/") or self.path.startswith("//"):
            raise AppError("invalid_url", "Invalid request URL")
        port = self.server.server_address[1]
        if self.headers.get("Host") not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
            raise AppError("invalid_host", "Use the loopback API address", 403)
        for header in [
            "Host",
            "Authorization",
            "Content-Length",
            "Idempotency-Key",
            "Origin",
            "Content-Type",
        ]:
            if len(self.headers.get_all(header, [])) > 1:
                raise AppError("invalid_headers", "Duplicate request header")
        url = urlsplit(self.path)
        path = url.path
        method = self.command
        static = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/app.js": ("app.js", "text/javascript; charset=utf-8"),
            "/style.css": ("style.css", "text/css; charset=utf-8"),
            "/favicon.svg": ("favicon.svg", "image/svg+xml"),
        }
        if method == "GET" and path in static:
            filename, mime = static[path]
            return self.send((ASSETS / filename).read_bytes(), mime=mime)
        if method == "POST" and path == "/api/browser/session":
            if not self.same_origin() or self.headers.get("Sec-Fetch-Site", "same-origin") != "same-origin":
                raise AppError("browser_origin_denied", "Open the web interface directly on localhost", 403)
            fields(self.body(1024), [])
            self.server.browser_expires = time.monotonic() + 7200
            return self.send(
                {"authenticated": True},
                extra={
                    "Set-Cookie": f"wechat_cli_browser={self.server.browser_secret}; HttpOnly; SameSite=Strict; Path=/; Max-Age=7200"
                },
            )
        supplied = self.headers.get("Authorization", "")
        bearer = supplied.startswith("Bearer ") and hmac.compare_digest(
            hashlib.sha256(supplied[7:].encode()).digest(),
            hashlib.sha256(self.server.token.encode()).digest(),
        )
        cookie = SimpleCookie()
        cookie.load(self.headers.get("Cookie", ""))
        browser = cookie.get("wechat_cli_browser")
        browser = bool(
            browser
            and self.server.browser_expires > time.monotonic()
            and hmac.compare_digest(browser.value, self.server.browser_secret)
        )
        if not bearer and not browser:
            raise AppError("unauthorized", "Bearer authentication required", 401)
        if not bearer and method != "GET" and not self.same_origin():
            raise AppError("browser_origin_denied", "Browser mutations require the local page origin", 403)
        if path not in ROUTES:
            raise AppError("not_found", "Unknown API route", 404)
        if method not in ROUTES[path]:
            return self.send({"error": "method_not_allowed"}, 405, extra={"Allow": ", ".join(ROUTES[path])})
        query = parse_qs(url.query, keep_blank_values=True)
        native = self.server.native
        if path == "/api/health":
            self.query(query, [])
            return self.send(
                {
                    "status": "ok",
                    "service": "wechat-cli",
                    "instance": self.server.instance,
                    "wechatConnected": native.observed.get("connected", False),
                }
            )
        if path == "/api/capabilities":
            self.query(query, [])
            return self.send(
                {
                    "protocol": "wechat-macos",
                    "nativeBridge": True,
                    "independentClient": False,
                    "chatHistory": True,
                    "sendMessages": True,
                    "mediaPreviews": True,
                    "voiceToText": "experimental_native_task",
                    "imageTextExtraction": "wechat_local_ocr",
                    "fileSending": "wechat_native_ui",
                    "voiceSending": "wechat_native_queue",
                }
            )
        if path == "/api/openapi.json":
            self.query(query, [])
            return self.send(openapi())
        if path in {"/api/native/status", "/api/login"}:
            self.query(query, [])
            return self.send(native.status())
        if path in {"/api/native/chats", "/api/chats"}:
            self.query(query, [])
            return self.send(native.chats())
        if method == "GET" and path in {"/api/native/messages", "/api/messages"}:
            input = self.query(query, ["chatId", "limit"])
            chat = chat_id(input.get("chatId"))
            limit = input.get("limit", "50")
            if not re.fullmatch(r"[1-9]\d{0,2}", limit) or not 1 <= int(limit) <= 100:
                raise AppError("invalid_query", "limit must be between 1 and 100")
            return self.send(native.messages(chat, int(limit)))
        if path == "/api/native/controls":
            self.query(query, [])
            return self.send(self.server.controls.invoke("status"))
        if path == "/api/native/media":
            input = self.query(query, ["chatId", "id", "variant"])
            chat, identifier = chat_id(input.get("chatId")), message_id(input.get("id"))
            variant = input.get("variant", "full")
            if variant not in {"full", "preview"}:
                raise AppError("invalid_query", "variant must be full or preview")
            data, mime, name = read_media(native, chat, identifier, variant)
            return self.send(
                data,
                mime=mime,
                extra={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name, safe='')}"}
                if name
                else {},
            )
        if path == "/api/native/voice/transcript":
            input = self.query(query, ["chatId", "id"])
            return self.send(transcript(native, chat_id(input.get("chatId")), message_id(input.get("id"))))
        self.query(query, [])
        input = self.body(12 * 1024**2 if path in {"/api/messages/file", "/api/messages/voice"} else 65536)
        if path == "/api/server/stop":
            fields(input, [])
            self.send({"stopping": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        chat = chat_id(input.get("chatId"))
        if path in {"/api/messages/send", "/api/messages", "/api/messages/prepare"}:
            fields(input, ["chatId", "text"])
            text = input["text"]
            if not isinstance(text, str) or not text.strip() or len(text) > 4096 or "\0" in text:
                raise AppError("invalid_input", "Text must contain 1–4096 characters without NUL bytes")
            if path == "/api/messages/prepare":
                result = native.request("prepareText", input)
                if (
                    result.get("prepared") is not True
                    or result.get("queued") is not False
                    or result.get("receiverVerified") is not True
                ):
                    raise AppError("native_invalid_response", "Text preparation could not be verified", 502)
                return self.send(result)
            return self.send(self.once(path, input, lambda: native.send(chat, text)))
        if path == "/api/messages/file":
            fields(input, ["chatId", "name", "dataBase64"])
            return self.send(self.once(path, input, lambda: self.server.controls.send_file(native, input)))
        if path == "/api/messages/voice":
            prepared = prepare_voice(input)
            return self.send(
                self.once(
                    path,
                    input,
                    lambda: {
                        **receipt(native.request("sendVoice", prepared), chat),
                        "durationMs": prepared["durationMs"],
                    },
                )
            )
        fields(input, ["chatId", "id"])
        identifier = message_id(input["id"])
        if path == "/api/native/attachments/download":
            return self.send(
                self.once(
                    path, input, lambda: self.server.controls.download_attachment(native, chat, identifier)
                )
            )
        if path == "/api/native/voice/transcribe":
            result = self.once(path, input, lambda: transcript(native, chat, identifier, True))
            return self.send(result, 202 if result["status"] == "pending" else 200)
        if path == "/api/native/images/text":
            message = find_message(native, chat, identifier)
            if message["kind"] != "image":
                raise AppError("invalid_media_type", "Choose an image message")
            data, _, _ = read_media(native, chat, identifier)
            dimensions = validate_image(data)
            result = native.request("extractImageText", {"dataBase64": base64.b64encode(data).decode()})
            if (
                result.get("recognition") != "wechat"
                or result.get("processing") != "local"
                or not isinstance(result.get("text"), str)
                or len(result["text"]) > 1048576
                or not isinstance(result.get("lines"), list)
                or len(result["lines"]) > 10000
                or any(not isinstance(line, str) or len(line) > 65536 for line in result["lines"])
            ):
                raise AppError("native_invalid_response", "Invalid OCR result", 502)
            return self.send({"source": "native", **input, **result, **dimensions, "status": "completed"})
        raise AppError("not_found", "Unknown API route", 404)

    def dispatch(self):
        try:
            self.handle_api()
        except AppError as error:
            self.send(error.as_dict(), error.status)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        except Exception:
            self.send({"error": "internal_error", "message": "The operation failed"}, 500)

    do_GET = do_POST = do_DELETE = do_PUT = do_PATCH = do_OPTIONS = do_HEAD = dispatch


def openapi():
    string = {"type": "string"}
    chat = {"type": "string", "minLength": 1, "maxLength": 1024}
    identifier = {
        "type": "string",
        "pattern": "^[0-9]{1,20}$",
        "description": "Exact message ID; never convert to a float",
    }
    properties = {
        "chatId": chat,
        "id": identifier,
        "text": {"type": "string", "minLength": 1, "maxLength": 4096},
        "name": {"type": "string", "minLength": 1, "maxLength": 200},
        "dataBase64": {"type": "string", "contentEncoding": "base64", "description": "At most 8 MiB decoded"},
        "format": {"type": "string", "enum": ["silk", "wav", "pcm_s16le"]},
        "sampleRate": {"type": "integer", "enum": [8000, 12000, 16000, 24000, 32000, 44100, 48000]},
    }
    json_body = {
        "/api/messages": ["chatId", "text"],
        "/api/messages/send": ["chatId", "text"],
        "/api/messages/prepare": ["chatId", "text"],
        "/api/messages/file": ["chatId", "name", "dataBase64"],
        "/api/messages/voice": ["chatId", "dataBase64", "format", "sampleRate"],
        "/api/native/images/text": ["chatId", "id"],
        "/api/native/voice/transcribe": ["chatId", "id"],
        "/api/native/attachments/download": ["chatId", "id"],
        "/api/server/stop": [],
    }
    idempotent = {
        "/api/messages",
        "/api/messages/send",
        "/api/messages/file",
        "/api/messages/voice",
        "/api/native/voice/transcribe",
        "/api/native/attachments/download",
    }
    queries = {
        "/api/messages": {
            "chatId": chat,
            "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 50},
        },
        "/api/native/messages": {
            "chatId": chat,
            "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 50},
        },
        "/api/native/media": {
            "chatId": chat,
            "id": identifier,
            "variant": {"type": "string", "enum": ["full", "preview"], "default": "full"},
        },
        "/api/native/voice/transcript": {"chatId": chat, "id": identifier},
    }
    paths = {}
    for path, methods in ROUTES.items():
        paths[path] = {}
        for method in methods:
            operation = {
                "operationId": method.lower() + path.replace("/", "_"),
                "summary": path,
                "responses": {
                    "200": {"description": "Success"},
                    "400": {"description": "Invalid input"},
                    "401": {"description": "Authentication required"},
                    "409": {"description": "Native state or idempotency conflict"},
                    "424": {"description": "App or runtime not configured"},
                    "502": {"description": "Native operation failed or send outcome uncertain"},
                    "504": {"description": "Native operation timed out"},
                },
            }
            parameters = []
            if method == "GET":
                parameters.extend(
                    {"name": name, "in": "query", "required": name in {"chatId", "id"}, "schema": schema}
                    for name, schema in queries.get(path, {}).items()
                )
            if method == "POST" and path in idempotent:
                parameters.append(
                    {
                        "name": "Idempotency-Key",
                        "in": "header",
                        "required": True,
                        "description": "Reuse with identical input for retries; retained for 24 hours",
                        "schema": {"type": "string", "pattern": "^[A-Za-z0-9_-]{8,128}$"},
                    }
                )
            if parameters:
                operation["parameters"] = parameters
            if method == "POST" and path in json_body:
                names = json_body[path]
                schema = {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {name: properties[name] for name in names},
                }
                if names:
                    schema["required"] = [name for name in names if name != "sampleRate"]
                operation["requestBody"] = {
                    "required": True,
                    "content": {"application/json": {"schema": schema}},
                }
            if path == "/api/native/media":
                operation["responses"]["200"]["content"] = {"application/octet-stream": {"schema": string}}
            if path == "/api/native/voice/transcribe":
                operation["responses"]["202"] = {
                    "description": "Recognition pending; poll /api/native/voice/transcript"
                }
            paths[path][method.lower()] = operation
    return {
        "openapi": "3.1.0",
        "info": {"title": "wechat-cli local API", "version": "0.1.0"},
        "servers": [{"url": "/"}],
        "security": [{"bearerAuth": []}],
        "components": {"securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer"}}},
        "paths": paths,
    }
