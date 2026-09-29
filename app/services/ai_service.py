import logging
from datetime import date

from langchain.agents import create_agent
from langchain_core.messages import AIMessage

from app.ai.models import get_chat_model
from app.ai.tools import build_tools
from app.config import settings
from app.exceptions import AIUpstreamError
from app.schemas.ai import AskResponse, TokenUsage, ToolCall
from app.schemas.auth import AuthenticatedClient

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a weather assistant. Use the available tools to fetch weather data; "
    "never guess current conditions. Only answer weather-related questions and "
    "politely decline anything else. If a tool returns an error, tell the user "
    "what went wrong instead of inventing data. Temperatures are in Celsius and "
    "wind speeds in km/h. Today's date is {today}."
)


async def ask_weather_assistant(
    question: str, client: AuthenticatedClient
) -> AskResponse:
    """Answer a natural-language question, calling weather tools as needed."""
    agent = create_agent(
        get_chat_model(),
        tools=build_tools(client.scopes),
        system_prompt=SYSTEM_PROMPT.format(today=date.today().isoformat()),
    )

    try:
        state = await agent.ainvoke(
            {"messages": [{"role": "user", "content": question}]},
            config={"recursion_limit": settings.ai_max_steps},
        )
    except Exception as exc:  # provider SDKs raise their own error hierarchies
        logger.exception("AI agent run failed")
        raise AIUpstreamError("AI agent run failed") from exc

    ai_messages = [m for m in state["messages"] if isinstance(m, AIMessage)]
    tool_calls = [
        ToolCall(name=call["name"], args=call["args"])
        for message in ai_messages
        for call in message.tool_calls
    ]
    usage = TokenUsage(
        input_tokens=sum((m.usage_metadata or {}).get("input_tokens", 0) for m in ai_messages),
        output_tokens=sum((m.usage_metadata or {}).get("output_tokens", 0) for m in ai_messages),
    )
    logger.info(
        "AI answered with %d tool call(s), %d input / %d output tokens",
        len(tool_calls),
        usage.input_tokens,
        usage.output_tokens,
    )
    return AskResponse(
        answer=ai_messages[-1].text if ai_messages else "",
        tool_calls=tool_calls,
        usage=usage,
    )
