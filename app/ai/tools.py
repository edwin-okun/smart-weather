from typing import Annotated, Any

from langchain_core.tools import BaseTool, tool
from pydantic import Field

from app.exceptions import LocationNotFoundError, UpstreamServiceError
from app.permissions import WEATHER_HISTORY_READ, WEATHER_READ
from app.schemas.weather import WeatherLocation
from app.services.weather import get_weather_for_city, get_weather_history


@tool(parse_docstring=True)
async def get_current_weather(
    city: Annotated[str, Field(min_length=1, max_length=100)],
    country_code: Annotated[str, Field(pattern=r"^[A-Za-z]{2}$")] = "KE",
) -> dict[str, Any]:
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
    return {
        "location": _place(result.location),
        **_conditions(result.weather),
    }


@tool(parse_docstring=True)
async def list_weather_history(
    limit: Annotated[int, Field(ge=1, le=20)] = 5,
    city: Annotated[str | None, Field(min_length=1, max_length=100)] = None,
    country_code: Annotated[str | None, Field(pattern=r"^[A-Za-z]{2}$")] = None,
) -> list[dict[str, Any]]:
    """List recent weather lookups previously saved by this service, newest first.

    These are past readings only, not current conditions: to compare an earlier
    lookup with now, also call get_current_weather.

    Args:
        limit: Maximum number of lookups to return (1-20).
        city: Only return lookups for this city (case-insensitive), e.g. "Nairobi".
            Use it when the question is about one city's earlier lookups.
        country_code: Only return lookups in this ISO 3166-1 alpha-2 country, e.g.
            "FR". Pass it with city when the name exists in several countries,
            e.g. Paris, France vs Paris, Texas (US).
    """
    items = await get_weather_history(limit=limit, city=city, country_code=country_code)
    return [
        {
            "location": _place(item.location),
            "looked_up_at": item.created_at.isoformat(),
            **_conditions(item.weather),
        }
        for item in items
    ]


# Tool results go back into the model's context, so return only what it needs to
# answer (not the raw Open-Meteo payload): fewer tokens, and less third-party text
# for a prompt injection to hide in.
def _place(location: WeatherLocation) -> str:
    return ", ".join(part for part in (location.name, location.country) if part)


def _conditions(weather: dict[str, Any]) -> dict[str, Any]:
    current = weather.get("current") or {}
    units = weather.get("current_units") or {}
    return {
        "observed_at": current.get("time"),
        "timezone": weather.get("timezone"),
        "current": {
            key: f"{value} {units[key]}" if key in units else value
            for key, value in current.items()
            if key not in ("time", "interval")
        },
    }


# Each tool requires the same scope as the equivalent HTTP endpoint, so the
# agent can never read data the caller could not read directly.
TOOL_SCOPES: list[tuple[BaseTool, str]] = [
    (get_current_weather, WEATHER_READ),
    (list_weather_history, WEATHER_HISTORY_READ),
]
TOOL_REQUIRED_SCOPES = frozenset(required for _, required in TOOL_SCOPES)


def build_tools(scopes: set[str] | frozenset[str]) -> list[BaseTool]:
    return [t for t, required in TOOL_SCOPES if required in scopes]
