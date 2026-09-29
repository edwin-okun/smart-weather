from functools import lru_cache

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from app.config import settings


@lru_cache
def get_chat_model() -> BaseChatModel:
    """Build the chat model lazily so the app can start without AI credentials.

    `settings.ai_model` is a LangChain "provider:model" string, e.g.
    "openai:gpt-4o-mini" or "anthropic:claude-haiku-4-5-20251001". Swapping
    providers is a config change plus installing the provider's langchain package.
    """
    kwargs: dict = {
        "temperature": settings.ai_temperature,
        "max_tokens": settings.ai_max_output_tokens,
        "timeout": settings.ai_timeout,
        "max_retries": settings.ai_max_retries,
    }
    api_key = settings.ai_api_key
    if api_key is None and settings.ai_model.startswith("openai:"):
        api_key = settings.openai_api_key
    if api_key:
        kwargs["api_key"] = api_key
    return init_chat_model(settings.ai_model, **kwargs)
