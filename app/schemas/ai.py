from pydantic import BaseModel


class ChatCompletionResult(BaseModel):
    content: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
