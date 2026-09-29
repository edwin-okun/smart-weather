from typing import Any

from langchain_core.tools import BaseTool, tool

from app.exceptions import LocationNotFoundError, UpstreamServiceError
from app.permissions import WEATHER_HISTORY_READ, WEATHER_READ
from app.services.weather import get_weather_for_city, get_weather_history


@tool
async def get_current_weather(city: str, country_code: str = "KE") -> dict[str, Any]:
    """Get the current weather for a city.

    Args:
        city: Name of the city, e.g. "Nairobi".
        country_code: ISO 3166-1 alpha-2 country code of the city, e.g. "KE".
    """
    try:
        result = await get_weather_for_city(city=city, country_code=country_code)
    except LocationNotFoundError as exc:
        return {"error": str(exc)}
    except UpstreamServiceError as exc:
        return {"error": f"Weather service unavailable: {exc}"}
    return result.model_dump(mode="json")


@tool
async def list_weather_history(limit: int = 10) -> list[dict[str, Any]]:
    """List recent weather lookups previously saved by this service.

    Args:
        limit: Maximum number of lookups to return (1-100).
    """
    items = await get_weather_history(limit=max(1, min(limit, 100)))
    return [item.model_dump(mode="json") for item in items]


# Each tool requires the same scope as the equivalent HTTP endpoint, so the
# agent can never read data the caller could not read directly.
TOOL_SCOPES: list[tuple[BaseTool, str]] = [
    (get_current_weather, WEATHER_READ),
    (list_weather_history, WEATHER_HISTORY_READ),
]


def build_tools(scopes: set[str]) -> list[BaseTool]:
    return [t for t, required in TOOL_SCOPES if required in scopes]
