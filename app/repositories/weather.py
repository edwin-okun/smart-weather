from app.models.weather import WeatherLookup
from app.schemas.weather import WeatherLocation


async def create_weather_lookup(
    city: str,
    country_code: str,
    location: WeatherLocation,
    weather: dict,
) -> WeatherLookup:
    return await WeatherLookup.create(
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
    limit: int = 20,
    city: str | None = None,
    country_code: str | None = None,
) -> list[WeatherLookup]:
    lookups = WeatherLookup.all()
    if city is not None:
        lookups = lookups.filter(city__iexact=city)
    if country_code is not None:
        lookups = lookups.filter(country_code=country_code.upper())
    return await lookups.limit(limit)
