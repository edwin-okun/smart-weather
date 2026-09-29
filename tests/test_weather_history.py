import unittest
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from app import db
from app.clients import open_meteo_client
from app.main import app
from app.models.auth import ApiClient
from app.models.weather import WeatherLookup
from app.permissions import WEATHER_HISTORY_READ, WEATHER_READ
from app.repositories.weather import create_weather_lookup
from app.schemas.auth import AuthenticatedClient
from app.schemas.weather import WeatherLocation
from app.security import utc_now
from app.services import ai_service
from app.services.auth import register_api_client
from app.services.weather import get_weather_history, prune_expired_weather_lookups

GEOCODING_PAYLOAD = {
    "results": [
        {
            "name": "Nairobi",
            "country": "Kenya",
            "country_code": "KE",
            "latitude": -1.28,
            "longitude": 36.82,
            "timezone": "Africa/Nairobi",
        }
    ]
}
FORECAST_PAYLOAD = {
    "timezone": "Africa/Nairobi",
    "current_units": {"time": "iso8601", "temperature_2m": "°C"},
    "current": {"time": "2026-09-30T12:00", "temperature_2m": 21.0},
}
LOCATION = WeatherLocation(name="Nairobi", country="Kenya", latitude=-1.28, longitude=36.82)


class ToolCallingFakeModel(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def _mock_open_meteo():
    """Patch the Open-Meteo client so no request leaves the process."""
    return (
        patch.object(open_meteo_client, "search_city", AsyncMock(return_value=GEOCODING_PAYLOAD)),
        patch.object(open_meteo_client, "get_forecast", AsyncMock(return_value=FORECAST_PAYLOAD)),
    )


async def _record(api_client_id: int | None, city: str, age: timedelta = timedelta()) -> int:
    lookup = await create_weather_lookup(
        api_client_id=api_client_id,
        city=city,
        country_code="KE",
        location=LOCATION,
        weather=FORECAST_PAYLOAD,
    )
    if age:
        # created_at is auto_now_add, so backdate it after the insert.
        await WeatherLookup.filter(id=lookup.id).update(created_at=utc_now() - age)
    return lookup.id


async def _client_pk(client_id: str) -> int:
    return (await ApiClient.get(client_id=client_id)).id


async def _lookups() -> list[tuple[int | None, str]]:
    return [
        (row.client_id, row.city) for row in await WeatherLookup.all().order_by("id")
    ]


async def _lookup_ids() -> set[int]:
    return set(await WeatherLookup.all().values_list("id", flat=True))


async def _clear() -> None:
    await WeatherLookup.all().delete()
    await ApiClient.all().delete()


class WeatherHistoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        db.TORTOISE_ORM["connections"]["default"] = "sqlite://:memory:"
        cls.client_context = TestClient(app)
        cls.client = cls.client_context.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client_context.__exit__(None, None, None)

    def setUp(self) -> None:
        self.client.portal.call(_clear)

    def _register(self, name: str) -> tuple[int, dict[str, str]]:
        """Create an API client; return its primary key and auth headers."""
        scopes = [WEATHER_READ, WEATHER_HISTORY_READ]
        created = self.client.portal.call(register_api_client, name, scopes)
        token = self.client.post(
            "/oauth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": created.client_id,
                "client_secret": created.client_secret,
                "scope": " ".join(scopes),
            },
        )
        self.assertEqual(token.status_code, 200)
        pk = self.client.portal.call(_client_pk, created.client_id)
        return pk, {"Authorization": f"Bearer {token.json()['access_token']}"}

    def _history(self, headers: dict[str, str]) -> list[str]:
        response = self.client.get("/weather/history", headers=headers)
        self.assertEqual(response.status_code, 200)
        return [item["city"] for item in response.json()]

    def _get_weather(self, city: str, headers: dict[str, str]) -> None:
        search, forecast = _mock_open_meteo()
        with search, forecast:
            response = self.client.get("/weather", params={"city": city}, headers=headers)
        self.assertEqual(response.status_code, 200)

    def test_lookup_is_recorded_under_the_calling_client(self) -> None:
        client_a, headers_a = self._register("Client A")

        self._get_weather("Nairobi", headers_a)

        self.assertEqual(self.client.portal.call(_lookups), [(client_a, "Nairobi")])
        self.assertEqual(self._history(headers_a), ["Nairobi"])

    def test_clients_only_see_their_own_lookups(self) -> None:
        _, headers_a = self._register("Client A")
        client_b, headers_b = self._register("Client B")
        self._get_weather("Nairobi", headers_a)
        self._get_weather("Mombasa", headers_a)
        self._get_weather("Kisumu", headers_b)
        # A row from before lookups were client-scoped belongs to nobody.
        self.client.portal.call(_record, None, "Legacy")

        self.assertEqual(self._history(headers_a), ["Mombasa", "Nairobi"])
        self.assertEqual(self._history(headers_b), ["Kisumu"])

        # Deleting a client deletes its history with it (ON DELETE CASCADE).
        self.client.portal.call(ApiClient.filter(id=client_b).delete)
        remaining = [city for _, city in self.client.portal.call(_lookups)]
        self.assertEqual(remaining, ["Nairobi", "Mombasa", "Legacy"])

    def test_expired_lookups_are_hidden_and_pruned(self) -> None:
        client_a, headers_a = self._register("Client A")
        expired = self.client.portal.call(_record, client_a, "Expired", timedelta(days=31))
        legacy_expired = self.client.portal.call(_record, None, "Legacy", timedelta(days=31))
        recent = self.client.portal.call(_record, client_a, "Recent", timedelta(days=29))

        # Hidden before any prune has run: history applies the cutoff itself.
        self.assertEqual(self._history(headers_a), ["Recent"])
        self.assertIn(expired, self.client.portal.call(_lookup_ids))

        # Recording a new lookup prunes expired rows, whoever they belonged to.
        self._get_weather("Nairobi", headers_a)

        ids = self.client.portal.call(_lookup_ids)
        self.assertNotIn(expired, ids)
        self.assertNotIn(legacy_expired, ids)
        self.assertIn(recent, ids)
        self.assertEqual(self._history(headers_a), ["Nairobi", "Recent"])

    def test_retention_window_follows_setting(self) -> None:
        client_a, headers_a = self._register("Client A")
        self.client.portal.call(_record, client_a, "Two days old", timedelta(days=2))

        with patch("app.services.weather.settings.weather_history_retention_days", 1):
            self.assertEqual(self._history(headers_a), [])
        self.assertEqual(self._history(headers_a), ["Two days old"])

    def test_prune_is_bounded_per_call(self) -> None:
        client_a, _ = self._register("Client A")
        for i in range(3):
            self.client.portal.call(_record, client_a, f"Old {i}", timedelta(days=40 + i))

        with patch("app.services.weather.settings.weather_history_prune_batch_size", 2):
            self.assertEqual(self.client.portal.call(prune_expired_weather_lookups), 2)
            # The oldest go first.
            self.assertEqual(
                [city for _, city in self.client.portal.call(_lookups)], ["Old 0"]
            )
            self.assertEqual(self.client.portal.call(prune_expired_weather_lookups), 1)
            self.assertEqual(self.client.portal.call(prune_expired_weather_lookups), 0)

    def test_ai_tools_record_and_read_under_the_calling_client(self) -> None:
        client_a, _ = self._register("Client A")
        client_b, _ = self._register("Client B")
        self.client.portal.call(_record, client_b, "Kisumu")
        model = ToolCallingFakeModel(
            messages=iter(
                [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {"name": "get_current_weather", "args": {"city": "Nairobi"}, "id": "1"}
                        ],
                    ),
                    AIMessage(
                        content="",
                        tool_calls=[
                            {"name": "list_weather_history", "args": {"limit": 5}, "id": "2"}
                        ],
                    ),
                    AIMessage(content="You looked up Nairobi."),
                ]
            )
        )
        caller = AuthenticatedClient(
            id=client_a,
            client_id="a",
            name="Client A",
            scopes={WEATHER_READ, WEATHER_HISTORY_READ},
        )
        seen_history: list[str] = []

        async def spy_history(**kwargs):
            items = await get_weather_history(**kwargs)
            seen_history.extend(item.city for item in items)
            return items

        search, forecast = _mock_open_meteo()
        ai_service._get_agent.cache_clear()
        self.addCleanup(ai_service._get_agent.cache_clear)
        with (
            search,
            forecast,
            patch.object(ai_service, "get_chat_model", return_value=model),
            patch("app.ai.tools.get_weather_history", side_effect=spy_history) as history,
        ):
            self.client.portal.call(ai_service.ask_weather_assistant, "What did I look up?", caller)

        self.assertIn((client_a, "Nairobi"), self.client.portal.call(_lookups))
        history.assert_awaited_once_with(
            api_client_id=client_a, limit=5, city=None, country_code=None
        )
        # The tool saw the lookup it just made, and none of client B's.
        self.assertEqual(seen_history, ["Nairobi"])
