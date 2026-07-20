from typing import Any
from datetime import datetime

from pydantic import BaseModel


class WeatherLocation(BaseModel):
    name: str
    country: str | None = None
    country_code: str | None = None
    latitude: float
    longitude: float
    timezone: str | None = None


class WeatherResponse(BaseModel):
    location: WeatherLocation
    weather: dict[str, Any]


class WeatherHistoryItem(BaseModel):
    id: int
    city: str
    country_code: str
    location: WeatherLocation
    weather: dict[str, Any]
    created_at: datetime
