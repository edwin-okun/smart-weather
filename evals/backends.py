"""Fixture-driven fakes for the weather service functions the agent's tools call.

Evals must be reproducible and must not hit Open-Meteo or write to the real
database, so `app.ai.tools.get_weather_for_city` and `get_weather_history` are
patched for the whole run. Cases run concurrently, so each case's fixtures live
in a context variable that the patched functions read: asyncio tasks inherit it,
and every case sees only its own data.
"""

import copy
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime
from typing import Any
from unittest.mock import patch

from app.exceptions import LocationNotFoundError, UpstreamServiceError
from app.schemas.weather import WeatherHistoryItem, WeatherLocation, WeatherResponse
from evals.dataset import Case, Dataset, FixtureError, LocationFixture, fill_dates

_active: ContextVar["CaseBackend | None"] = ContextVar("eval_backend", default=None)


class CaseBackend:
    """Serves one case's fixtures the way the real weather service would."""

    def __init__(self, dataset: Dataset, case: Case, today: date):
        self.today = today
        self.locations = [dataset.fixtures.locations[name] for name in case.fixtures]
        self.history = dataset.fixtures.history[case.history] if case.history else None
        self._all_locations = dataset.fixtures.locations
        # What the tools asked the service for, for the report and tests.
        self.calls: list[dict[str, Any]] = []

    async def get_weather_for_city(self, city: str, country_code: str = "KE") -> WeatherResponse:
        self.calls.append({"function": "get_weather_for_city", "city": city, "country_code": country_code})
        # Like the real geocoder: exact name within the country, first result wins.
        query = city.strip().casefold()
        for fixture in self.locations:
            names = {fixture.geocoding["name"].casefold(), *(a.casefold() for a in fixture.aliases)}
            if query in names and fixture.geocoding["country_code"].upper() == country_code.upper():
                if fixture.error is not None:
                    raise _exception(fixture.error)
                return WeatherResponse(location=_location(fixture), weather=fill_dates(fixture.forecast, self.today))
        raise LocationNotFoundError(f"No location found for city {city}")

    async def get_weather_history(self, limit: int = 20, city: str | None = None) -> list[WeatherHistoryItem]:
        self.calls.append({"function": "get_weather_history", "limit": limit, "city": city})
        if self.history is None:
            return []
        if self.history.error is not None:
            raise _exception(self.history.error)
        items = []
        entries = [e for e in self.history.entries if city is None or e.city.lower() == city.lower()]
        for index, entry in enumerate(entries[:limit]):
            fixture = self._all_locations[entry.location]
            weather = copy.deepcopy(fixture.forecast or {})
            weather["current"] = {**weather.get("current", {}), **entry.current}
            items.append(
                WeatherHistoryItem(
                    id=len(self.history.entries) - index,
                    city=entry.city,
                    country_code=entry.country_code,
                    location=_location(fixture),
                    weather=fill_dates(weather, self.today),
                    created_at=datetime.fromisoformat(fill_dates(entry.looked_up_at, self.today)),
                )
            )
        return items


def _location(fixture: LocationFixture) -> WeatherLocation:
    # Same mapping as app.services.weather._find_location.
    geo = fixture.geocoding
    return WeatherLocation(
        name=geo["name"],
        country=geo.get("country"),
        country_code=geo.get("country_code"),
        latitude=geo["latitude"],
        longitude=geo["longitude"],
        timezone=geo.get("timezone"),
    )


def _exception(error: FixtureError) -> Exception:
    match error.type:
        case "not_found":
            return LocationNotFoundError(error.message)
        case "upstream":
            return UpstreamServiceError(error.message)
        case _:
            return RuntimeError(error.message)


def _backend() -> CaseBackend:
    backend = _active.get()
    if backend is None:
        # Never fall through to the real service from an eval.
        raise RuntimeError("no eval backend is active for this task")
    return backend


async def _fake_get_weather_for_city(city: str, country_code: str = "KE") -> WeatherResponse:
    return await _backend().get_weather_for_city(city=city, country_code=country_code)


async def _fake_get_weather_history(limit: int = 20, city: str | None = None) -> list[WeatherHistoryItem]:
    return await _backend().get_weather_history(limit=limit, city=city)


@contextmanager
def patched_weather_services() -> Iterator[None]:
    """Route the agent's tools to the active case backend for the whole run."""
    with (
        patch("app.ai.tools.get_weather_for_city", _fake_get_weather_for_city),
        patch("app.ai.tools.get_weather_history", _fake_get_weather_history),
    ):
        yield


@contextmanager
def use_backend(backend: CaseBackend) -> Iterator[CaseBackend]:
    token = _active.set(backend)
    try:
        yield backend
    finally:
        _active.reset(token)
