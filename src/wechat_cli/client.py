"""Small synchronous HTTP client used by the CLI and available to Python callers."""

import http.client
import json
import urllib.error
import urllib.request
from urllib.parse import urlencode

from wechat_cli.core.config import api_token
from wechat_cli.core.errors import AppError
from wechat_cli.core.network import NoRedirect


class ApiClient:
    def __init__(self, directory, port=8787, timeout=180):
        self.base = f"http://127.0.0.1:{port}"
        self.token = api_token(directory)
        self.timeout = timeout
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def request(self, path, *, method="GET", data=None, key=None, binary=False):
        if not path.startswith("/api/") or "#" in path or "\r" in path or "\n" in path:
            raise AppError("invalid_api_path", "Use a local /api/ path")
        headers = {"Authorization": "Bearer " + self.token}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if key:
            headers["Idempotency-Key"] = key
        request = urllib.request.Request(
            self.base + path,
            data=None if data is None else json.dumps(data).encode(),
            headers=headers,
            method=method,
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read(128 * 1024**2 + 1)
                if len(raw) > 128 * 1024**2:
                    raise AppError("response_too_large", "API response exceeds 128 MiB", 413)
                return raw if binary else json.loads(raw)
        except urllib.error.HTTPError as error:
            try:
                value = json.loads(error.read(65536))
                raise AppError(value["error"], value.get("message", value["error"]), error.code) from None
            except (ValueError, KeyError):
                raise AppError("api_http_error", f"API returned HTTP {error.code}", error.code) from None
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException):
            raise AppError("api_unavailable", "Local API is unavailable; run wechat-cli start", 503) from None

    def chats(self):
        return self.request("/api/native/chats")

    def messages(self, chat_id, limit=50):
        return self.request("/api/native/messages?" + urlencode({"chatId": chat_id, "limit": limit}))

    def send(self, chat_id, text, *, idempotency_key):
        return self.request(
            "/api/messages/send", method="POST", data={"chatId": chat_id, "text": text}, key=idempotency_key
        )

    def compose_moment(self, payload, *, idempotency_key, post=False):
        return self.request(
            "/api/moments/post" if post else "/api/moments/compose",
            method="POST",
            data=payload,
            key=idempotency_key,
        )

    def moment_action(self, session_id, action, *, idempotency_key=None):
        return self.request(
            "/api/moments/action",
            method="POST",
            data={"sessionId": session_id, "action": action},
            key=idempotency_key,
        )
