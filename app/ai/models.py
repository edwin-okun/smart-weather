from functools import lru_cache

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from app.config import settings


def get_chat_model(model: str | None = None) -> BaseChatModel:
    """Build the chat model lazily so the app can start without AI credentials.

    `model` defaults to `settings.ai_model`, a LangChain "provider:model" string,
    e.g. "openai:gpt-4o-mini" or "anthropic:claude-haiku-4-5-20251001". Swapping
    providers is a config change plus installing the provider's langchain package.
    Passing `model` builds another model with the same settings (the evals use
    it for a judge model).
    """
    return _build_chat_model(model or settings.ai_model)


@lru_cache
def _build_chat_model(model: str) -> BaseChatModel:
    kwargs: dict = {
        "temperature": settings.ai_temperature,
        "max_tokens": settings.ai_max_output_tokens,
        "timeout": settings.ai_timeout,
        "max_retries": settings.ai_max_retries,
    }
    api_key = settings.ai_api_key
    if api_key is None and model.startswith("openai:"):
        api_key = settings.openai_api_key
    if api_key:
        kwargs["api_key"] = api_key
    return init_chat_model(model, **kwargs)
