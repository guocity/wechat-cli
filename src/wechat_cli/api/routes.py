"""Thin HTTP handlers over the shared service and typed backend contract."""

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse, Response

from wechat_cli.core.errors import AppError
from wechat_cli.core.media import prepare_voice, read_media
from wechat_cli.core.models import receipt
from wechat_cli.core.services import WeChatService, transcript

from .schemas import Empty, FileInput, HistoryQuery, MediaQuery, MessageRef, TextInput, VoiceInput

router = APIRouter(prefix="/api")


def service(request: Request):
    if not request.app.state.accepting:
        raise AppError("server_stopping", "WeChat closed; the server is shutting down", 503)
    return request.app.state.service


Service = Annotated[WeChatService, Depends(service)]
Key = Annotated[str | None, Header(alias="Idempotency-Key", pattern=r"^[A-Za-z0-9_-]{8,128}$")]


@router.get("/native/status")
@router.get("/login", include_in_schema=False)
def status(svc: Service, query: Annotated[Empty, Query()]):
    return svc.backend.status()


@router.get("/capabilities")
def capabilities(svc: Service, query: Annotated[Empty, Query()]):
    return svc.backend.capabilities()


@router.get("/native/chats")
@router.get("/chats", include_in_schema=False)
def chats(svc: Service, query: Annotated[Empty, Query()]):
    return svc.backend.chats()


@router.get("/native/messages")
@router.get("/messages", include_in_schema=False)
def messages(svc: Service, query: Annotated[HistoryQuery, Query()]):
    return svc.backend.messages(query.chatId, query.limit)


@router.get("/native/controls")
def controls(svc: Service, query: Annotated[Empty, Query()]):
    return svc.backend.controls_status()


@router.post("/messages/prepare")
def prepare_text(svc: Service, payload: TextInput):
    return svc.prepare_text(payload.chatId, payload.text)


@router.post("/messages/send")
@router.post("/messages", include_in_schema=False)
def send_text(svc: Service, payload: TextInput, key: Key = None):
    return svc.once(
        "/api/messages/send",
        key,
        payload.model_dump(),
        lambda: svc.backend.send(payload.chatId, payload.text),
    )


@router.post("/messages/file")
def send_file(svc: Service, payload: FileInput, key: Key = None):
    data = payload.model_dump()
    return svc.once("/api/messages/file", key, data, lambda: svc.backend.send_file(data))


@router.post("/messages/voice")
def send_voice(svc: Service, payload: VoiceInput, key: Key = None):
    if not svc.backend.capabilities().get("voiceSending"):
        raise AppError("native_feature_unavailable", "Voice sending is unavailable on this backend", 424)
    data = payload.model_dump(exclude_none=True)
    prepared = (
        prepare_voice(data, svc.backend.convert_audio)
        if hasattr(svc.backend, "convert_audio")
        else prepare_voice(data)
    )
    return svc.once(
        "/api/messages/voice",
        key,
        data,
        lambda: {
            **receipt(svc.backend.send_voice(prepared), payload.chatId),
            "durationMs": prepared["durationMs"],
        },
    )


@router.get(
    "/native/media", response_class=Response, responses={200: {"content": {"application/octet-stream": {}}}}
)
@router.get("/media", response_class=Response, include_in_schema=False)
def media(svc: Service, query: Annotated[MediaQuery, Query()]):
    data, mime, name = read_media(svc.backend, query.chatId, query.id, query.variant)
    headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name, safe='')}"} if name else {}
    return Response(data, media_type=mime, headers=headers)


@router.get("/native/voice/transcript")
def voice_transcript(svc: Service, query: Annotated[MessageRef, Query()]):
    return transcript(svc.backend, query.chatId, query.id)


@router.post(
    "/native/voice/transcribe",
    responses={202: {"description": "Recognition pending; poll the transcript endpoint"}},
)
def transcribe(svc: Service, payload: MessageRef, key: Key = None):
    result = svc.once(
        "/api/native/voice/transcribe",
        key,
        payload.model_dump(),
        lambda: transcript(svc.backend, payload.chatId, payload.id, True),
    )
    return JSONResponse(result, status_code=202 if result["status"] == "pending" else 200)


@router.post("/native/images/text")
@router.post("/ocr", include_in_schema=False)
def ocr(svc: Service, payload: MessageRef):
    return svc.ocr(payload.chatId, payload.id)


@router.post("/native/attachments/download")
def download(svc: Service, payload: MessageRef, key: Key = None):
    return svc.once(
        "/api/native/attachments/download",
        key,
        payload.model_dump(),
        lambda: svc.backend.download_attachment(payload.chatId, payload.id),
    )
