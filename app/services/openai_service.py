import logging

from app.clients import openai_chat_client
from app.exceptions import AIUpstreamError
from app.schemas.ai import ChatCompletionResult

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gpt-4o"
DEFAULT_TEMPERATURE = 0.7
DEFAULT_MAX_COMPLETION_TOKENS = 50
WEATHER_ASSISTANT_SYSTEM_PROMPT = (
    "You are a helpful assistant that provides weather information."
)

# USD per token. See https://openai.com/api/pricing
MODEL_TOKEN_PRICES_USD: dict[str, tuple[float, float]] = {
    "gpt-4o": (0.15 / 1_000_000, 0.6 / 1_000_000),
}


def ask_weather_assistant(
    question: str,
    *,
    model: str = DEFAULT_MODEL,
    temperature: float = DEFAULT_TEMPERATURE,
    max_completion_tokens: int = DEFAULT_MAX_COMPLETION_TOKENS,
) -> ChatCompletionResult:
    """Ask the weather assistant persona a natural-language question."""
    return _run_chat_completion(
        system_prompt=WEATHER_ASSISTANT_SYSTEM_PROMPT,
        user_prompt=question,
        model=model,
        temperature=temperature,
        max_completion_tokens=max_completion_tokens,
    )


def _run_chat_completion(
    *,
    system_prompt: str,
    user_prompt: str,
    model: str,
    temperature: float,
    max_completion_tokens: int,
) -> ChatCompletionResult:
    response = openai_chat_client.create_chat_completion(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=temperature,
        max_completion_tokens=max_completion_tokens,
    )

    if response.usage is None:
        raise AIUpstreamError("OpenAI chat completion response is missing usage data")

    content = response.choices[0].message.content or ""
    input_tokens = response.usage.prompt_tokens
    output_tokens = response.usage.completion_tokens
    cost_usd = _estimate_cost(model, input_tokens, output_tokens)

    logger.info(
        "OpenAI %s completion used %d input / %d output tokens ($%.6f)",
        model,
        input_tokens,
        output_tokens,
        cost_usd,
    )
    return ChatCompletionResult(
        content=content,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
    )


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    try:
        input_price, output_price = MODEL_TOKEN_PRICES_USD[model]
    except KeyError:
        raise ValueError(f"No pricing configured for model {model!r}") from None
    return input_tokens * input_price + output_tokens * output_price
