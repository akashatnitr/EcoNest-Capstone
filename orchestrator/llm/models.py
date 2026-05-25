from typing import Literal

from pydantic import BaseModel


class LLMMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class LLMResponse(BaseModel):
    content: str
    model: str
    raw_response: dict | None = None
