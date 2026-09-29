from datetime import datetime

from app.models.weather import WeatherLookup
from app.schemas.weather import WeatherLocation


async def create_weather_lookup(
    api_client_id: int,
    city: str,
    country_code: str,
    location: WeatherLocation,
    weather: dict,
) -> WeatherLookup:
    return await WeatherLookup.create(
        client_id=api_client_id,
        city=city,
        country_code=country_code.upper(),
        location_name=location.name,
        location_country=location.country,
        location_timezone=location.timezone,
        latitude=location.latitude,
        longitude=location.longitude,
        weather=weather,
    )


async def list_weather_lookups(
    api_client_id: int,
    created_after: datetime,
    limit: int = 20,
    city: str | None = None,
    country_code: str | None = None,
) -> list[WeatherLookup]:
    """Latest lookups made by one API client, newest first.

    Rows with a null client (recorded before history was client-scoped) never
    match, so they are visible to nobody.
    """
    lookups = WeatherLookup.filter(client_id=api_client_id, created_at__gte=created_after)
    if city is not None:
        lookups = lookups.filter(city__iexact=city)
    if country_code is not None:
        lookups = lookups.filter(country_code=country_code.upper())
    return await lookups.order_by("-created_at", "-id").limit(limit)


async def delete_weather_lookups_before(cutoff: datetime, batch_size: int) -> int:
    """Delete up to `batch_size` of the oldest lookups created before `cutoff`.

    Bounded so a large backlog is worked off a batch at a time instead of in one
    long write that holds the SQLite lock. Selecting ids first keeps it portable:
    SQLite does not support DELETE ... LIMIT by default.
    """
    expired_ids = (
        await WeatherLookup.filter(created_at__lt=cutoff)
        .order_by("created_at")
        .limit(batch_size)
        .values_list("id", flat=True)
    )
    if not expired_ids:
        return 0
    return await WeatherLookup.filter(id__in=expired_ids).delete()
