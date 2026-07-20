from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from app.exceptions import LocationNotFoundError, UpstreamServiceError
from app.schemas.weather import WeatherHistoryItem, WeatherResponse
from app.services.weather import get_weather_for_city, get_weather_history

router = APIRouter(tags=["weather"])


@router.get(
    "/weather/history",
    response_model=list[WeatherHistoryItem],
    operation_id="list_weather_history",
    summary="List saved weather lookups",
    description=(
        "Returns recent successful weather lookups saved in SQLite. "
        "Use this to inspect cached lookup history without calling Open-Meteo."
    ),
)
async def list_weather_history(
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    return await get_weather_history(limit=limit)


@router.get(
    "/weather",
    response_model=WeatherResponse,
    operation_id="get_weather",
    summary="Get current weather for a city",
    description=(
        "Looks up a city through Open-Meteo, fetches current weather by coordinates, "
        "and stores the successful lookup in SQLite."
    ),
)
async def get_weather(
    city: Annotated[str, Query(description="Name of the city")],
    country_code: Annotated[
        str,
        Query(
            min_length=2,
            max_length=2,
            description="ISO 3166-1 alpha-2 country code",
        ),
    ] = "KE",
):
    try:
        return await get_weather_for_city(city=city, country_code=country_code)
    except LocationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except UpstreamServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
