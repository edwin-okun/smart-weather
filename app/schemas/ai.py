from typing import Any

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class ToolCall(BaseModel):
    name: str
    args: dict[str, Any]


class TokenUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0


class AskResponse(BaseModel):
    answer: str
    tool_calls: list[ToolCall]
    usage: TokenUsage
