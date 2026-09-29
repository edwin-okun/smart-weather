import logging
import os

from langchain_core.tracers.langchain import get_client

from app.config import settings

logger = logging.getLogger(__name__)

FLUSH_TIMEOUT_SECONDS = 10.0


def configure_tracing() -> bool:
    """Export LangSmith settings to the environment; return whether tracing is on.

    Must run before the first traced call: LangSmith reads its environment
    lazily and caches the result.
    """
    if not settings.langsmith_tracing:
        return False
    if not settings.langsmith_api_key:
        logger.warning("LANGSMITH_TRACING is enabled but LANGSMITH_API_KEY is not set; tracing disabled")
        return False

    env = {
        "LANGSMITH_TRACING": "true",
        "LANGSMITH_API_KEY": settings.langsmith_api_key,
        "LANGSMITH_PROJECT": settings.langsmith_project,
        "LANGSMITH_HIDE_INPUTS": str(settings.langsmith_hide_inputs).lower(),
        "LANGSMITH_HIDE_OUTPUTS": str(settings.langsmith_hide_outputs).lower(),
    }
    if settings.langsmith_endpoint:
        env["LANGSMITH_ENDPOINT"] = settings.langsmith_endpoint
    os.environ.update(env)

    logger.info("LangSmith tracing enabled for project %s", settings.langsmith_project)
    return True


def flush_traces(timeout: float = FLUSH_TIMEOUT_SECONDS) -> None:
    """Wait up to `timeout` seconds for queued traces to send; call on shutdown.

    Bounded so an unreachable LangSmith cannot hang shutdown. This blocks, so
    call it from a worker thread when on the event loop.
    """
    get_client().flush(timeout=timeout)
