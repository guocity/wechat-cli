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


class ChannelLink(Empty):
    type: Literal["none", "article", "redEnvelope"]
    url: str = Field(default="", max_length=4096)

    @model_validator(mode="after")
    def valid_url(self):
        from urllib.parse import urlsplit

        value = urlsplit(self.url)
        if self.type != "none" and (
            value.scheme != "https" or not value.hostname or value.username or value.password
        ):
            raise ValueError("Use an absolute HTTPS link without credentials")
        if self.type == "none" and self.url:
            raise ValueError("A cleared link cannot contain a URL")
        return self


class ChannelOptions(Empty):
    location: str | None = Field(default=None, max_length=1024)
    locationQuery: str | None = Field(default=None, max_length=200)
    link: ChannelLink | None = None
    activity: str | None = Field(default=None, max_length=1024)
    activityQuery: str | None = Field(default=None, max_length=200)
    scheduleAt: str | None = Field(default=None, max_length=64)
    declareOriginal: bool | None = None
    acceptOriginalityTerms: bool | None = None
    videoLabel: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def original_terms_require_declaration(self):
        if self.acceptOriginalityTerms is True and self.declareOriginal is not True:
            raise ValueError("acceptOriginalityTerms requires declareOriginal=true")
        return self

    @field_validator("scheduleAt")
    @classmethod
    def valid_schedule(cls, value):
        from datetime import datetime

        if value is not None and value != "immediate":
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if stamp.tzinfo is None or stamp.second or stamp.microsecond:
                raise ValueError("Use ISO 8601 with timezone and whole minutes, or immediate")
        return value


class ChannelVideoInput(Empty):
    dataBase64: str | None = Field(default=None, min_length=1, max_length=89478488)
    videoPath: str | None = Field(default=None, min_length=1, max_length=4096)
    description: str = Field(default="", max_length=4096)
    title: str = Field(default="", max_length=16)
    waitSeconds: int = Field(default=60, ge=0, le=120)
    options: ChannelOptions | None = None

    @model_validator(mode="after")
    def single_video_source(self):
        if (self.dataBase64 is None) == (self.videoPath is None):
            raise ValueError("Supply exactly one of dataBase64 or videoPath")
        if self.videoPath is not None and (not self.videoPath.startswith("/") or "\0" in self.videoPath):
            raise ValueError("videoPath must be an absolute local path without NUL")
        return self

    @field_validator("description", "title")
    @classmethod
    def no_nul(cls, value):
        if "\0" in value:
            raise ValueError("Text cannot contain NUL")
        return value


class ChannelPublishInput(Empty):
    sessionId: str = Field(pattern=r"^[a-f0-9-]{36}$")


class ChannelVideosQuery(Empty):
    limit: int = Field(default=20, ge=1, le=100)
    cursor: str | None = Field(default=None, min_length=1, max_length=4096)

    @field_validator("limit", mode="before")
    @classmethod
    def canonical_limit(cls, value):
        if isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,2}", value):
            return int(value)
        return value

    @field_validator("cursor")
    @classmethod
    def valid_cursor(cls, value):
        if value is not None and re.search(r"[\x00-\x1f\x7f]", value):
            raise ValueError("Cursor cannot contain control characters")
        return value


class ChannelDeleteInput(Empty):
    videoId: str = Field(min_length=1, max_length=1024)

    @field_validator("videoId")
    @classmethod
    def valid_video_id(cls, value):
        if not value.strip() or re.search(r"[\x00-\x1f\x7f]", value):
            raise ValueError("Use a nonempty video ID without control characters")
        return value


class ChannelUpdateInput(ChannelPublishInput):
    description: str | None = Field(default=None, max_length=4096)
    title: str | None = Field(default=None, max_length=16)
    options: ChannelOptions | None = None
    nativeAction: Literal["cover", "topic", "mention"] | None = None

    @field_validator("description", "title")
    @classmethod
    def no_nul(cls, value):
        if value is not None and "\0" in value:
            raise ValueError("Text cannot contain NUL")
        return value


class ChannelChoicesInput(ChannelPublishInput):
    kind: Literal["location", "activity", "link", "videoLabel"]
    query: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def searchable(self):
        if self.query is not None and self.kind not in {"location", "activity"}:
            raise ValueError("Only location and activity support search")
        return self
