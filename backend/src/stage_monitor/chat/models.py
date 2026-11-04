from typing import Literal

from pydantic import Field, model_validator

from ..contracts import Model


class RoomCreate(Model):
    kind: Literal["channel", "direct"] = "channel"
    name: str | None = Field(default=None, min_length=1, max_length=64, pattern=r"^[\w -]+$")
    other_user_id: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def validate_room(self):
        if self.kind == "channel" and (not self.name or self.other_user_id):
            raise ValueError("Channels require a name and no recipient")
        if self.kind == "direct" and (not self.other_user_id or self.name):
            raise ValueError("Direct rooms require a recipient and no channel name")
        return self


class MessageCreate(Model):
    text: str = Field(min_length=1, max_length=4000)
    client_message_id: str = Field(min_length=1, max_length=120, pattern=r"^[a-zA-Z0-9_.-]+$")

    @model_validator(mode="after")
    def nonempty(self):
        if not self.text.strip():
            raise ValueError("Message cannot be blank")
        return self


class ReadMarker(Model):
    message_id: int = Field(ge=1)


class Typing(Model):
    typing: bool
