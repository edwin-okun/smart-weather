import asyncio
import unittest
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, ToolMessage
from pydantic import Field, ValidationError

from app.ai.tools import build_tools, get_current_weather, list_weather_history
from app.exceptions import (
    AIStepLimitError,
    AITimeoutError,
    AIUpstreamError,
    LocationNotFoundError,
)
from app.permissions import AI_ASK, WEATHER_HISTORY_READ, WEATHER_READ
from app.routers import ai as ai_router
from app.schemas.ai import AskRequest
from app.schemas.auth import AuthenticatedClient
from app.schemas.weather import WeatherHistoryItem, WeatherLocation, WeatherResponse
from app.services import ai_service

OPEN_METEO_PAYLOAD = {
    "latitude": -1.25,
    "longitude": 36.875,
    "generationtime_ms": 0.05,
    "timezone": "Africa/Nairobi",
    "elevation": 1683.0,
    "current_units": {"time": "iso8601", "interval": "seconds", "temperature_2m": "°C"},
    "current": {"time": "2026-09-29T12:00", "interval": 900, "temperature_2m": 21.0},
}
NAIROBI = WeatherLocation(name="Nairobi", country="Kenya", latitude=-1.28, longitude=36.82)


class ToolCallingFakeModel(GenericFakeChatModel):
    """Scripted chat model that records the messages it was called with."""

    received: list[list[Any]] = Field(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, *args, **kwargs):
        self.received.append(list(messages))
        return super()._generate(messages, *args, **kwargs)


class FailingModel(ToolCallingFakeModel):
    def _generate(self, messages, *args, **kwargs):
        raise RuntimeError("provider exploded: sk-secret")


class SlowModel(ToolCallingFakeModel):
    async def _agenerate(self, messages, *args, **kwargs):
        await asyncio.sleep(5)


def _client(*scopes: str) -> AuthenticatedClient:
    return AuthenticatedClient(id=1, client_id="c", name="c", scopes=set(scopes))


def _tool_call(name: str, args: dict[str, Any], call_id: str = "call_1") -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


def _model(*messages: AIMessage, cls=ToolCallingFakeModel) -> ToolCallingFakeModel:
    return cls(messages=iter(messages))


class BuildToolsTests(unittest.TestCase):
    def test_tools_follow_scopes(self) -> None:
        names = lambda s: {t.name for t in build_tools(s)}
        self.assertEqual(names({AI_ASK}), set())
        self.assertEqual(names({WEATHER_READ}), {"get_current_weather"})
        self.assertEqual(
            names({WEATHER_READ, WEATHER_HISTORY_READ}),
            {"get_current_weather", "list_weather_history"},
        )


class ToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_current_weather_is_trimmed(self) -> None:
        weather = WeatherResponse(location=NAIROBI, weather=OPEN_METEO_PAYLOAD)
        with patch("app.ai.tools.get_weather_for_city", AsyncMock(return_value=weather)):
            result = await get_current_weather.ainvoke({"city": "Nairobi"})

        self.assertEqual(
            result,
            {
                "location": "Nairobi, Kenya",
                "observed_at": "2026-09-29T12:00",
                "timezone": "Africa/Nairobi",
                "current": {"temperature_2m": "21.0 °C"},
            },
        )

    async def test_history_is_trimmed(self) -> None:
        item = WeatherHistoryItem(
            id=1,
            city="nairobi",
            country_code="KE",
            location=NAIROBI,
            weather=OPEN_METEO_PAYLOAD,
            created_at=datetime(2026, 9, 29, 9, 0, tzinfo=UTC),
        )
        with patch("app.ai.tools.get_weather_history", AsyncMock(return_value=[item])) as fetch:
            result = await list_weather_history.ainvoke({"limit": 3})

        fetch.assert_awaited_once_with(limit=3)
        self.assertEqual(result[0]["location"], "Nairobi, Kenya")
        self.assertEqual(result[0]["looked_up_at"], "2026-09-29T09:00:00+00:00")
        self.assertNotIn("elevation", str(result))

    async def test_known_weather_errors_become_error_results(self) -> None:
        with patch(
            "app.ai.tools.get_weather_for_city",
            AsyncMock(side_effect=LocationNotFoundError("No location found for city Atlantis")),
        ):
            result = await get_current_weather.ainvoke({"city": "Atlantis"})

        self.assertEqual(result, {"error": "No location found for city Atlantis"})

    async def test_arguments_are_validated(self) -> None:
        for args in ({"city": "Nairobi", "country_code": "KEN"}, {"city": ""}, {"city": "x" * 101}):
            with self.subTest(args=args), self.assertRaises(ValidationError):
                await get_current_weather.ainvoke(args)
        with self.assertRaises(ValidationError):
            await list_weather_history.ainvoke({"limit": 500})


class AskWeatherAssistantTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        # The compiled agent is cached with whatever model was current; tests swap models.
        ai_service._get_agent.cache_clear()
        self.addCleanup(ai_service._get_agent.cache_clear)

    async def _ask(self, model, *scopes: str, question: str = "How is Nairobi?"):
        with patch.object(ai_service, "get_chat_model", return_value=model):
            return await ai_service.ask_weather_assistant(question, _client(*scopes))

    async def test_agent_calls_weather_tool_and_answers(self) -> None:
        model = _model(
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "get_current_weather",
                        "args": {"city": "Nairobi", "country_code": "KE"},
                        "id": "call_1",
                    }
                ],
                usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            ),
            AIMessage(
                content="It is 21C in Nairobi.",
                usage_metadata={"input_tokens": 20, "output_tokens": 6, "total_tokens": 26},
            ),
        )
        weather = WeatherResponse(location=NAIROBI, weather=OPEN_METEO_PAYLOAD)
        with patch("app.ai.tools.get_weather_for_city", AsyncMock(return_value=weather)) as fetch:
            result = await self._ask(model, WEATHER_READ)

        fetch.assert_awaited_once_with(city="Nairobi", country_code="KE")
        self.assertEqual(result.answer, "It is 21C in Nairobi.")
        self.assertEqual([c.name for c in result.tool_calls], ["get_current_weather"])
        self.assertEqual(result.usage.input_tokens, 30)
        self.assertEqual(result.usage.output_tokens, 11)
        system_prompt = model.received[0][0].content
        self.assertIn("untrusted", system_prompt)
        self.assertIn("Today's date is", system_prompt)

    async def test_tool_outside_scope_is_not_executed(self) -> None:
        model = _model(
            _tool_call("list_weather_history", {"limit": 5}),
            AIMessage(content="I cannot access history."),
        )
        with patch("app.ai.tools.get_weather_history", AsyncMock()) as fetch:
            result = await self._ask(model, WEATHER_READ)

        fetch.assert_not_awaited()
        tool_message = model.received[1][-1]
        self.assertIsInstance(tool_message, ToolMessage)
        self.assertEqual(tool_message.status, "error")
        self.assertEqual(result.answer, "I cannot access history.")

    async def test_invalid_tool_arguments_are_returned_to_model(self) -> None:
        model = _model(
            _tool_call("get_current_weather", {"city": "Nairobi", "country_code": "Kenya"}),
            AIMessage(content="Please give a two-letter country code."),
        )
        with patch("app.ai.tools.get_weather_for_city", AsyncMock()) as fetch:
            result = await self._ask(model, WEATHER_READ)

        fetch.assert_not_awaited()
        self.assertEqual(model.received[1][-1].status, "error")
        self.assertEqual(result.answer, "Please give a two-letter country code.")

    async def test_unexpected_tool_error_is_hidden_from_model(self) -> None:
        model = _model(
            _tool_call("list_weather_history", {"limit": 5}),
            AIMessage(content="History is unavailable right now."),
        )
        with (
            patch(
                "app.ai.tools.get_weather_history",
                AsyncMock(side_effect=RuntimeError("database is locked")),
            ),
            self.assertLogs(ai_service.logger, "ERROR"),
        ):
            result = await self._ask(model, WEATHER_HISTORY_READ)

        tool_message = model.received[1][-1]
        self.assertEqual(tool_message.status, "error")
        self.assertNotIn("database is locked", tool_message.text)
        self.assertEqual(result.answer, "History is unavailable right now.")

    async def test_step_limit_raises(self) -> None:
        model = _model(
            *(_tool_call("get_current_weather", {"city": "Nairobi"}, f"call_{i}") for i in range(20))
        )
        weather = WeatherResponse(location=NAIROBI, weather=OPEN_METEO_PAYLOAD)
        with (
            patch("app.ai.tools.get_weather_for_city", AsyncMock(return_value=weather)),
            self.assertLogs(ai_service.logger, "WARNING"),
            self.assertRaises(AIStepLimitError),
        ):
            await self._ask(model, WEATHER_READ)

    async def test_provider_failure_raises_generic_error(self) -> None:
        with self.assertLogs(ai_service.logger, "ERROR"), self.assertRaises(AIUpstreamError) as ctx:
            await self._ask(_model(cls=FailingModel), WEATHER_READ)

        self.assertNotIn("sk-secret", str(ctx.exception))

    async def test_run_timeout_raises(self) -> None:
        with (
            patch.object(ai_service.settings, "ai_request_timeout", 0.05),
            self.assertLogs(ai_service.logger, "WARNING"),
            self.assertRaises(AITimeoutError),
        ):
            await self._ask(_model(cls=SlowModel), WEATHER_READ)

    async def test_empty_answer_raises(self) -> None:
        with self.assertLogs(ai_service.logger, "WARNING"), self.assertRaises(AIUpstreamError):
            await self._ask(_model(AIMessage(content="  ")), WEATHER_READ)

    async def test_agent_is_cached_per_tool_scope_set(self) -> None:
        with patch.object(ai_service, "get_chat_model", return_value=_model()) as get_model:
            read_only = ai_service._get_agent(frozenset({WEATHER_READ}))
            self.assertIs(read_only, ai_service._get_agent(frozenset({WEATHER_READ})))
            self.assertIsNot(read_only, ai_service._get_agent(frozenset()))
        self.assertEqual(get_model.call_count, 2)


class AskRouteTests(unittest.IsolatedAsyncioTestCase):
    async def _status_for(self, exc: Exception) -> int:
        with (
            patch.object(ai_router, "ask_weather_assistant", AsyncMock(side_effect=exc)),
            self.assertRaises(HTTPException) as ctx,
        ):
            await ai_router.ask(AskRequest(question="hi"), _client(AI_ASK))
        return ctx.exception.status_code

    async def test_errors_map_to_gateway_statuses(self) -> None:
        self.assertEqual(await self._status_for(AIUpstreamError("AI agent run failed")), 502)
        self.assertEqual(await self._status_for(AIStepLimitError("step limit")), 502)
        self.assertEqual(await self._status_for(AITimeoutError("timed out")), 504)

    def test_blank_question_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            AskRequest(question="   ")
        self.assertEqual(AskRequest(question="  hi ").question, "hi")
