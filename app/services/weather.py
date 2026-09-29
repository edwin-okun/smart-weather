import logging
from datetime import datetime, timedelta
from typing import Any

from pydantic import ValidationError

from app.clients import open_meteo_client
from app.config import settings
from app.exceptions import LocationNotFoundError, UpstreamServiceError
from app.repositories.weather import (
    create_weather_lookup,
    delete_weather_lookups_before,
    list_weather_lookups,
)
from app.schemas.weather import WeatherHistoryItem, WeatherLocation, WeatherResponse
from app.security import utc_now

logger = logging.getLogger(__name__)


async def get_weather_for_city(
    city: str, country_code: str = "KE", *, api_client_id: int
) -> WeatherResponse:
    """Fetch current weather and record the lookup under the calling API client.

    `api_client_id` is the ApiClient primary key (AuthenticatedClient.id).
    """
    location = await _find_location(city, country_code)
    forecast_data = await _fetch_forecast(location, city)
    await create_weather_lookup(
        api_client_id=api_client_id,
        city=city,
        country_code=country_code,
        location=location,
        weather=forecast_data,
    )
    await prune_expired_weather_lookups()

    logger.info("Weather data for city %s: %s", city, forecast_data)
    return WeatherResponse(location=location, weather=forecast_data)


async def get_weather_history(
    *,
    api_client_id: int,
    limit: int = 20,
    city: str | None = None,
    country_code: str | None = None,
) -> list[WeatherHistoryItem]:
    """The calling client's own lookups within the retention window, newest first."""
    # Filtering on the cutoff here means correctness never depends on when the
    # prune last ran.
    lookups = await list_weather_lookups(
        api_client_id=api_client_id,
        created_after=history_cutoff(),
        limit=limit,
        city=city,
        country_code=country_code,
    )
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


def history_cutoff() -> datetime:
    return utc_now() - timedelta(days=settings.weather_history_retention_days)


async def prune_expired_weather_lookups() -> int:
    """Delete one bounded batch of lookups past the retention window.

    Runs on every write, so pruning keeps pace with inserts in a long-running
    process without a scheduler, and the created_at index makes the common
    nothing-to-delete case a cheap index probe. Pruning is housekeeping: a
    failure is logged and never fails the lookup that triggered it.
    """
    try:
        deleted = await delete_weather_lookups_before(
            history_cutoff(), batch_size=settings.weather_history_prune_batch_size
        )
    except Exception:
        logger.exception("Pruning expired weather lookups failed")
        return 0
    if deleted:
        logger.info("Pruned %d expired weather lookups", deleted)
    return deleted


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
