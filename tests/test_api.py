import http.client
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from wechat_cli.core.errors import AppError
from wechat_cli.core.models import resolve_message


def raw(server, path, method="GET", body=None, headers=None, host=None):
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    headers = {"Host": host or f"127.0.0.1:{server.server_address[1]}", **(headers or {})}
    if isinstance(body, dict):
        body = json.dumps(body)
        headers.setdefault("Content-Type", "application/json")
    connection.request(method, path, body=body, headers=headers)
    response = connection.getresponse()
    result = response.status, response.headers, response.read()
    connection.close()
    return result


def test_auth_before_native_access(api):
    server, client, native = api
    for path in ["/api/native/chats", "/api/native/messages?chatId=x", "/api/native/status"]:
        assert raw(server, path)[0] == 401
    assert native.calls == []
    assert client.chats()["items"][0]["id"] == "filehelper"
    assert raw(server, "/api/health", host="attacker.example")[0] == 403


@pytest.mark.parametrize(
    "query",
    [
        "",
        "chatId=a&chatId=b",
        "chatId=x&limit=0",
        "chatId=x&limit=101",
        "chatId=x&limit=1.5",
        "chatId=%00",
        "chatId=x&path=/etc/passwd",
        "chatId=x&limit=01",
    ],
)
def test_history_validation(api, query):
    server, client, native = api
    with pytest.raises(AppError) as caught:
        client.request("/api/native/messages?" + query)
    assert caught.value.status == 400
    assert native.calls == []


def test_history_keeps_big_string_ids(api):
    _, client, _ = api
    result = client.messages("filehelper", 2)
    assert result["items"][0]["id"] == "18446744073709551615"


def test_methods_and_json_validation(api):
    server, client, native = api
    with pytest.raises(AppError) as caught:
        client.request("/api/native/chats", method="POST", data={})
    assert caught.value.status == 405
    headers = {"Authorization": "Bearer " + client.token}
    assert raw(server, "/api/messages/send", "POST", "{}", headers)[0] == 415
    headers["Content-Type"] = "application/json"
    for body in ["[]", "null", "{", '"text"']:
        assert raw(server, "/api/messages/send", "POST", body, headers)[0] == 400
    assert native.calls == []


@pytest.mark.parametrize(
    "input",
    [
        {"chatId": "", "text": "x"},
        {"chatId": "filehelper", "text": " "},
        {"chatId": "filehelper", "text": "x\0y"},
        {"chatId": "filehelper", "text": "x" * 4097},
        {"chatId": "filehelper", "text": "x", "extra": True},
    ],
)
def test_send_validation(api, input):
    _, client, native = api
    with pytest.raises(AppError):
        client.request("/api/messages/send", method="POST", data=input, key="test-send-1")
    assert native.calls == []


def test_idempotent_send_and_conflict(api):
    _, client, native = api

    def send():
        return client.send("filehelper", "hello", idempotency_key="same-key-123")

    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(lambda _: send(), range(4)))
    assert results.count(results[0]) == 4
    assert len([c for c in native.calls if c[0] == "send"]) == 1
    with pytest.raises(AppError) as caught:
        client.send("filehelper", "different", idempotency_key="same-key-123")
    assert caught.value.code == "idempotency_conflict"
    # Alias shares the same canonical operation key.
    assert (
        client.request(
            "/api/messages", method="POST", data={"text": "hello", "chatId": "filehelper"}, key="same-key-123"
        )
        == results[0]
    )
    native.account = "second-account"
    send()
    assert len([c for c in native.calls if c[0] == "send"]) == 2


def test_unknown_send_outcome_is_not_retried(api):
    _, client, native = api
    count = []

    def fail(chat, text):
        count.append(1)
        raise AppError("native_send_outcome_unknown", "Check history", 504)

    native.send = fail
    for _ in range(2):
        with pytest.raises(AppError) as caught:
            client.send("filehelper", "hello", idempotency_key="uncertain-key")
        assert caught.value.code == "native_send_outcome_unknown"
    assert count == [1]


def test_browser_session_requires_origin_and_blocks_csrf(api):
    server, client, native = api
    base = client.base
    assert raw(server, "/api/browser/session", "POST", {})[0] == 403
    status, headers, _ = raw(server, "/api/browser/session", "POST", {}, {"Origin": base})
    assert status == 200
    assert "HttpOnly" in headers["Set-Cookie"] and "samesite=strict" in headers["Set-Cookie"].lower()
    cookie = headers["Set-Cookie"].split(";")[0]
    assert raw(server, "/api/native/chats", headers={"Cookie": cookie})[0] == 200
    payload = {"chatId": "filehelper", "text": "test"}
    assert (
        raw(
            server,
            "/api/messages/send",
            "POST",
            payload,
            {"Cookie": cookie, "Idempotency-Key": "browser-key-1"},
        )[0]
        == 403
    )
    assert not any(c[0] == "send" for c in native.calls)
    assert (
        raw(
            server,
            "/api/messages/send",
            "POST",
            payload,
            {"Cookie": cookie, "Origin": base, "Idempotency-Key": "browser-key-1"},
        )[0]
        == 200
    )
    server.app.state.access.browser_expires = 0
    assert raw(server, "/api/native/chats", headers={"Cookie": cookie})[0] == 401


def test_static_assets_and_openapi(api):
    server, client, _ = api
    for path in ["/", "/app.js", "/style.css"]:
        status, headers, content = raw(server, path)
        assert status == 200 and content
        assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    assert client.request("/api/openapi.json")["openapi"] == "3.1.0"
    with pytest.raises(AppError) as caught:
        client.request("/api/not-a-route")
    assert caught.value.status == 404


def test_generated_schema_describes_typed_inputs_and_auth(api):
    _, client, _ = api
    document = client.request("/api/openapi.json")
    schema = document["components"]["schemas"]["TextInput"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["text"]["maxLength"] == 4096
    assert "HTTPBearer" in document["components"]["securitySchemes"]
    assert "requestBody" in document["paths"]["/api/messages/send"]["post"]


def test_upload_limit_and_post_query_rejected_before_native_access(api):
    server, client, native = api
    headers = {
        "Authorization": "Bearer " + client.token,
        "Content-Type": "application/json",
        "Content-Length": "70000",
    }
    assert raw(server, "/api/messages/send", "POST", "{}", headers)[0] == 413
    with pytest.raises(AppError) as caught:
        client.request(
            "/api/messages/send?extra=1",
            method="POST",
            data={"chatId": "filehelper", "text": "x"},
            key="post-query-key",
        )
    assert caught.value.code == "invalid_query"
    assert native.calls == []


def test_static_asset_query_cannot_select_arbitrary_file(api):
    server, _, _ = api
    status, _, content = raw(server, "/app.js?file=../../api-token&media=text/plain")
    assert status == 200
    assert content.startswith(b"'use strict';")


def test_transcript_read_never_starts_conversion(api):
    _, client, native = api
    native.items = [resolve_message({"id": "123", "type": 34, "content": "<msg><voicemsg/></msg>"})]
    result = client.request("/api/native/voice/transcript?chatId=filehelper&id=123")
    assert result["status"] == "unavailable" and result["conversionTriggered"] is False
    assert not any(c[0] == "transcribeVoice" for c in native.calls)
    assert (
        client.request(
            "/api/native/voice/transcribe",
            method="POST",
            data={"chatId": "filehelper", "id": "123"},
            key="transcript-key",
        )["status"]
        == "pending"
    )
    assert len([c for c in native.calls if c[0] == "transcribeVoice"]) == 1


def test_text_dry_run_does_not_call_send(api):
    _, client, native = api
    original = native.request

    def request(method, params=None):
        if method == "prepareText":
            native.calls.append((method, params))
            return {
                "source": "native",
                "prepared": True,
                "queued": False,
                "receiverVerified": True,
                "requestId": "dry-run",
            }
        return original(method, params)

    native.request = request
    result = client.request(
        "/api/messages/prepare", method="POST", data={"chatId": "filehelper", "text": "not sent"}
    )
    assert result["prepared"] is True and result["queued"] is False
    assert not any(c[0] in {"send", "sendText"} for c in native.calls)
