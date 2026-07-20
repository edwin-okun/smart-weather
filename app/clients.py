import logging
from typing import Any

import httpx

from app.config import settings
from app.exceptions import UpstreamServiceError

logger = logging.getLogger(__name__)

OPEN_METEO_GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
CURRENT_WEATHER_FIELDS = ",".join(
    [
        "temperature_2m",
        "relative_humidity_2m",
        "apparent_temperature",
        "precipitation",
        "weather_code",
        "wind_speed_10m",
        "wind_direction_10m",
    ]
)


weather_client = httpx.AsyncClient(timeout=settings.weather_client_timeout)


class OpenMeteoClient:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def search_city(self, city: str, country_code: str) -> dict[str, Any]:
        return await self._get_json(
            OPEN_METEO_GEOCODING_URL,
            params={
                "name": city,
                "count": 1,
                "countryCode": country_code.upper(),
                "format": "json",
            },
            request_type="geocoding",
            city=city,
        )

    async def get_forecast(
        self,
        latitude: float,
        longitude: float,
        city: str,
    ) -> dict[str, Any]:
        return await self._get_json(
            OPEN_METEO_FORECAST_URL,
            params={
                "latitude": latitude,
                "longitude": longitude,
                "current": CURRENT_WEATHER_FIELDS,
                "timezone": "auto",
            },
            request_type="forecast",
            city=city,
        )

    async def _get_json(
        self,
        url: str,
        params: dict[str, Any],
        request_type: str,
        city: str,
    ) -> dict[str, Any]:
        try:
            response = await self.client.get(url, params=params)
        except httpx.RequestError as exc:
            logger.exception("Open-Meteo %s request failed for city %s", request_type, city)
            raise UpstreamServiceError(f"Open-Meteo {request_type} request failed") from exc

        try:
            data = response.json()
        except ValueError as exc:
            logger.warning(
                "Open-Meteo %s returned a non-JSON response for city %s with status %s",
                request_type,
                city,
                response.status_code,
            )
            raise UpstreamServiceError(
                f"Open-Meteo {request_type} returned an invalid response"
            ) from exc

        if not isinstance(data, dict):
            raise UpstreamServiceError(
                f"Open-Meteo {request_type} returned an invalid response"
            )

        if response.is_error:
            logger.warning(
                "Open-Meteo %s failed for city %s with status %s: %s",
                request_type,
                city,
                response.status_code,
                data,
            )
            raise UpstreamServiceError(f"Open-Meteo {request_type} request failed")

        return data


open_meteo_client = OpenMeteoClient(weather_client)
