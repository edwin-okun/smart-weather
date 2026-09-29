import unittest
from datetime import timedelta

from app import db
from app.models.auth import ApiClient
from app.permissions import WEATHER_HISTORY_READ
from app.repositories.weather import create_weather_lookup, list_weather_lookups
from app.schemas.weather import WeatherLocation
from app.security import utc_now
from app.services.auth import register_api_client


def _location(name: str) -> WeatherLocation:
    return WeatherLocation(name=name, latitude=0.0, longitude=0.0)


class WeatherHistoryFilterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        db.TORTOISE_ORM["connections"]["default"] = "sqlite://:memory:"
        await db.init_db()
        self.addAsyncCleanup(db.close_db)
        created = await register_api_client("filters", [WEATHER_HISTORY_READ])
        self.api_client_id = (await ApiClient.get(client_id=created.client_id)).id
        self.cutoff = utc_now() - timedelta(days=1)
        for city, country_code in (("Nairobi", "KE"), ("Tokyo", "JP"), ("Nairobi", "KE"), ("London", "GB")):
            await self._record(city, country_code, {"current": {}})

    async def _record(self, city: str, country_code: str, weather: dict) -> None:
        await create_weather_lookup(self.api_client_id, city, country_code, _location(city), weather)

    async def _list(self, **filters):
        return await list_weather_lookups(
            api_client_id=self.api_client_id, created_after=self.cutoff, **filters
        )

    async def test_lists_newest_first_without_a_filter(self) -> None:
        lookups = await self._list(limit=3)
        self.assertEqual([lookup.city for lookup in lookups], ["London", "Nairobi", "Tokyo"])

    async def test_city_filter_is_case_insensitive_and_applies_before_limit(self) -> None:
        lookups = await self._list(limit=1, city="nAIROBI")
        self.assertEqual([lookup.city for lookup in lookups], ["Nairobi"])
        self.assertEqual(len(await self._list(limit=10, city="NAIROBI")), 2)

    async def test_country_filter_separates_duplicate_city_names(self) -> None:
        # Paris, Texas is looked up after Paris, France.
        await self._record("Paris", "FR", {"current": {"temperature_2m": 12.5}})
        await self._record("Paris", "US", {"current": {"temperature_2m": 31.0}})

        [newest] = await self._list(limit=1, city="paris")
        self.assertEqual(newest.country_code, "US")
        [france] = await self._list(limit=1, city="paris", country_code="fr")
        self.assertEqual((france.country_code, france.weather["current"]["temperature_2m"]), ("FR", 12.5))

    async def test_unknown_city_returns_nothing(self) -> None:
        self.assertEqual(await self._list(city="Atlantis"), [])
