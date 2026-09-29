from typing import Annotated, Any

from pydantic import BaseModel, StringConstraints


class AskRequest(BaseModel):
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


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
