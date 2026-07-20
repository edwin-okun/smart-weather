import logging
from typing import Any

from pydantic import ValidationError

from app.clients import open_meteo_client
from app.exceptions import LocationNotFoundError, UpstreamServiceError
from app.repositories.weather import create_weather_lookup, list_weather_lookups
from app.schemas.weather import WeatherHistoryItem, WeatherLocation, WeatherResponse

logger = logging.getLogger(__name__)


async def get_weather_for_city(city: str, country_code: str = "KE") -> WeatherResponse:
    location = await _find_location(city, country_code)
    forecast_data = await _fetch_forecast(location, city)
    await create_weather_lookup(
        city=city,
        country_code=country_code,
        location=location,
        weather=forecast_data,
    )

    logger.info("Weather data for city %s: %s", city, forecast_data)
    return WeatherResponse(location=location, weather=forecast_data)


async def get_weather_history(limit: int = 20) -> list[WeatherHistoryItem]:
    lookups = await list_weather_lookups(limit=limit)
    return [
        WeatherHistoryItem(
            id=lookup.id,
            city=lookup.city,
            country_code=lookup.country_code,
            location=WeatherLocation(
                name=lookup.location_name,
                country=lookup.location_country,
                country_code=lookup.country_code,
                latitude=lookup.latitude,
                longitude=lookup.longitude,
                timezone=lookup.location_timezone,
            ),
            weather=lookup.weather,
            created_at=lookup.created_at,
        )
        for lookup in lookups
    ]


async def _find_location(city: str, country_code: str) -> WeatherLocation:
    geocoding_data = await open_meteo_client.search_city(city, country_code)
    locations = geocoding_data.get("results", [])
    if not locations:
        raise LocationNotFoundError(f"No location found for city {city}")

    try:
        location = locations[0]
        return WeatherLocation(
            name=location["name"],
            country=location.get("country"),
            country_code=location.get("country_code"),
            latitude=location["latitude"],
            longitude=location["longitude"],
            timezone=location.get("timezone"),
        )
    except (KeyError, TypeError, ValidationError) as exc:
        raise UpstreamServiceError("Open-Meteo geocoding returned an invalid response") from exc


async def _fetch_forecast(location: WeatherLocation, city: str) -> dict[str, Any]:
    return await open_meteo_client.get_forecast(
        latitude=location.latitude,
        longitude=location.longitude,
        city=city,
    )
