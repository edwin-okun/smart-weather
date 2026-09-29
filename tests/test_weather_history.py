import unittest

from app import db
from app.repositories.weather import create_weather_lookup, list_weather_lookups
from app.schemas.weather import WeatherLocation


def _location(name: str) -> WeatherLocation:
    return WeatherLocation(name=name, latitude=0.0, longitude=0.0)


class WeatherHistoryFilterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        db.TORTOISE_ORM["connections"]["default"] = "sqlite://:memory:"
        await db.init_db()
        self.addAsyncCleanup(db.close_db)
        for city in ("Nairobi", "Tokyo", "Nairobi", "London"):
            await create_weather_lookup(city, "KE", _location(city), {"current": {}})

    async def test_lists_newest_first_without_a_filter(self) -> None:
        lookups = await list_weather_lookups(limit=3)
        self.assertEqual([lookup.city for lookup in lookups], ["London", "Nairobi", "Tokyo"])

    async def test_city_filter_is_case_insensitive_and_applies_before_limit(self) -> None:
        lookups = await list_weather_lookups(limit=1, city="nAIROBI")
        self.assertEqual([lookup.city for lookup in lookups], ["Nairobi"])
        self.assertEqual(len(await list_weather_lookups(limit=10, city="NAIROBI")), 2)

    async def test_unknown_city_returns_nothing(self) -> None:
        self.assertEqual(await list_weather_lookups(city="Atlantis"), [])
