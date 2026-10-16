"""Strict wire inputs. Keep message IDs as strings on every platform."""

import re
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator


def valid_chat(value):
    if re.search(r"[\x00-\x1f\x7f]", value) or value in {
        "@placeholder_foldgroup",
        "brandsessionholder",
        "brandservicesessionholder",
    }:
        raise ValueError("Invalid chat ID")
    return value


ChatId = Annotated[str, Field(strict=True, min_length=1, max_length=1024), AfterValidator(valid_chat)]
MessageId = Annotated[str, Field(strict=True, pattern=r"^[0-9]{1,20}$")]


class Empty(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class HistoryQuery(Empty):
    chatId: ChatId
    limit: int = Field(default=50, ge=1, le=100)

    @field_validator("limit", mode="before")
    @classmethod
    def canonical_limit(cls, value):
        if isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,2}", value):
            return int(value)
        return value


class MessageRef(Empty):
    chatId: ChatId
    id: MessageId


class MomentsQuery(Empty):
    limit: int = Field(default=20, ge=1, le=1000)

    @field_validator("limit", mode="before")
    @classmethod
    def canonical_limit(cls, value):
        if isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,3}", value):
            return int(value)
        return value


class MediaQuery(MessageRef):
    variant: Literal["full", "preview"] = "full"


class TextInput(Empty):
    chatId: ChatId
    text: str = Field(min_length=1, max_length=4096)

    @field_validator("text")
    @classmethod
    def valid_text(cls, value):
        if not value.strip() or "\0" in value:
            raise ValueError("Text must contain non-whitespace characters and no NUL")
        return value


class FileInput(Empty):
    chatId: ChatId
    name: str = Field(min_length=1, max_length=200)
    dataBase64: str = Field(min_length=1, max_length=11184812)


class VoiceInput(Empty):
    chatId: ChatId
    dataBase64: str = Field(min_length=1, max_length=11184812)
    format: Literal["silk", "wav", "pcm_s16le"]
    sampleRate: int | None = None


class MomentMedia(Empty):
    dataBase64: str = Field(min_length=1, max_length=89478488)


class MomentInput(Empty):
    text: str = Field(default="", max_length=4096)
    kind: Literal["images", "video"]
    media: list[MomentMedia] = Field(min_length=1, max_length=9)

    @model_validator(mode="after")
    def video_is_single(self):
        if self.kind == "video" and len(self.media) != 1:
            raise ValueError("Choose exactly one video")
        return self

    @field_validator("text")
    @classmethod
    def valid_caption(cls, value):
        if "\0" in value:
            raise ValueError("Caption cannot contain NUL")
        return value


class MomentAction(Empty):
    sessionId: str = Field(pattern=r"^[a-f0-9-]{36}$")
    action: Literal["status", "post", "cancel", "emoji", "mention", "visibility", "ai", "add-media"]
