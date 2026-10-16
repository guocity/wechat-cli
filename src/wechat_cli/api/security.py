"""Loopback authentication, browser CSRF protection and bounded request bodies."""

import asyncio
import hashlib
import hmac
import re
import secrets
import time
from collections import Counter
from http.cookies import CookieError, SimpleCookie

from starlette.responses import JSONResponse

from wechat_cli.core.config import api_token
from wechat_cli.core.errors import AppError

STATIC = {"/", "/app.js", "/style.css", "/favicon.svg"}
SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; media-src 'self' blob:; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
}


class Access:
    def __init__(self, directory):
        self.token = api_token(directory)
        self.browser_secret = secrets.token_hex(32)
        self.browser_expires = 0


class LocalSecurity:
    def __init__(self, app, access):
        self.app, self.access = app, access

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def secured_send(message):
            if message["type"] == "http.response.start":
                message["headers"] = list(message["headers"]) + [
                    (key.lower().encode(), value.encode()) for key, value in SECURITY_HEADERS.items()
                ]
            await send(message)

        try:
            await self.handle(scope, receive, secured_send)
        except AppError as error:
            await JSONResponse(error.as_dict(), status_code=error.status)(scope, receive, secured_send)

    async def handle(self, scope, receive, send):
        raw = scope["headers"]
        headers = {key.decode("latin1").lower(): value.decode("latin1") for key, value in raw}
        counts = Counter(key.lower() for key, _ in raw)
        if any(
            counts[key] > 1
            for key in [
                b"host",
                b"authorization",
                b"content-length",
                b"idempotency-key",
                b"origin",
                b"content-type",
                b"cookie",
            ]
        ):
            raise AppError("invalid_headers", "Duplicate request header")
        port = scope["server"][1]
        if headers.get("host") not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
            raise AppError("invalid_host", "Use the loopback API address", 403)
        path, method = scope["path"], scope["method"]
        if len(scope.get("raw_path", b"")) + len(scope.get("query_string", b"")) > 4096 or path.startswith(
            "//"
        ):
            raise AppError("invalid_url", "Invalid request URL")
        same_origin = headers.get("origin") == "http://" + headers["host"]
        session = path == "/api/browser/session" and method == "POST"
        public = method == "GET" and path in STATIC
        if session:
            if not same_origin or headers.get("sec-fetch-site", "same-origin") != "same-origin":
                raise AppError("browser_origin_denied", "Open the web interface directly on localhost", 403)
        elif not public:
            supplied = headers.get("authorization", "")
            bearer = supplied.startswith("Bearer ") and hmac.compare_digest(
                hashlib.sha256(supplied[7:].encode()).digest(),
                hashlib.sha256(self.access.token.encode()).digest(),
            )
            cookie = SimpleCookie()
            try:
                cookie.load(headers.get("cookie", ""))
            except CookieError:
                pass
            browser = cookie.get("wechat_cli_browser")
            authenticated = bool(
                browser
                and self.access.browser_expires > time.monotonic()
                and hmac.compare_digest(browser.value, self.access.browser_secret)
            )
            if not bearer and not authenticated:
                raise AppError("unauthorized", "Bearer authentication required", 401)
            if not bearer and method != "GET" and not same_origin:
                raise AppError(
                    "browser_origin_denied", "Browser mutations require the local page origin", 403
                )
        if method == "POST":
            if headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
                raise AppError("invalid_content_type", "Use application/json", 415)
            length = headers.get("content-length", "")
            if "transfer-encoding" in headers or not re.fullmatch(r"\d{1,9}", length):
                raise AppError("invalid_content_length", "Supply a bounded Content-Length", 411)
            maximum = (
                86 * 1024**2
                if path in {"/api/moments/post", "/api/moments/compose"}
                else 12 * 1024**2
                if path in {"/api/messages/file", "/api/messages/voice"}
                else 1024
                if session
                else 65536
            )
            if int(length) > maximum:
                raise AppError("body_too_large", "Request body exceeds the upload limit", 413)
            # Bound streamed bytes too, before FastAPI parses JSON/Pydantic models.
            chunks, size = [], 0
            while True:
                try:
                    message = await asyncio.wait_for(receive(), timeout=15)
                except TimeoutError:
                    raise AppError("request_timeout", "Request body timed out", 408) from None
                if message["type"] == "http.disconnect":
                    return
                chunk = message.get("body", b"")
                size += len(chunk)
                if size > maximum or size > int(length):
                    raise AppError("body_too_large", "Request body exceeds the upload limit", 413)
                chunks.append(chunk)
                if not message.get("more_body", False):
                    break
            if size != int(length):
                raise AppError("invalid_json", "Incomplete request body")

            async def buffered_receive():
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}

            return await self.app(scope, buffered_receive, send)
        return await self.app(scope, receive, send)
