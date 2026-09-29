import asyncio
import unittest
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import openai
from fastapi import HTTPException
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field, ValidationError

from app.ai.tools import (
    build_tools,
    get_current_weather,
    list_weather_history,
    run_config_for_client,
)
from app.config import Settings
from app.exceptions import (
    AIRateLimitError,
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
CLIENT_CONFIG = run_config_for_client(1)
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


def _openai_error(status: int, headers: dict[str, str] | None = None, body: dict | None = None):
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    response = httpx.Response(status, headers=headers or {}, request=request)
    cls = openai.RateLimitError if status == 429 else openai.APIStatusError
    return cls("provider said no", response=response, body=body)


class RaisingModel(ToolCallingFakeModel):
    """Raises the given error from an OpenAI-style provider, wrapped like LangChain does."""

    error: Any = None

    async def _agenerate(self, messages, *args, **kwargs):
        raise RuntimeError("model call failed") from self.error


class GateModel(ToolCallingFakeModel):
    """Blocks inside the model call until `gate` is set, to hold a run in flight."""

    gate: Any = None

    async def _agenerate(self, messages, *args, **kwargs):
        await self.gate.wait()
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="ok"))])


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
            result = await get_current_weather.ainvoke({"city": "Nairobi"}, CLIENT_CONFIG)

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
            result = await list_weather_history.ainvoke({"limit": 3}, CLIENT_CONFIG)

        fetch.assert_awaited_once_with(api_client_id=1, limit=3)
        self.assertEqual(result[0]["location"], "Nairobi, Kenya")
        self.assertEqual(result[0]["looked_up_at"], "2026-09-29T09:00:00+00:00")
        self.assertNotIn("elevation", str(result))

    async def test_known_weather_errors_become_error_results(self) -> None:
        with patch(
            "app.ai.tools.get_weather_for_city",
            AsyncMock(side_effect=LocationNotFoundError("No location found for city Atlantis")),
        ):
            result = await get_current_weather.ainvoke({"city": "Atlantis"}, CLIENT_CONFIG)

        self.assertEqual(result, {"error": "No location found for city Atlantis"})

    async def test_arguments_are_validated(self) -> None:
        for args in ({"city": "Nairobi", "country_code": "KEN"}, {"city": ""}, {"city": "x" * 101}):
            with self.subTest(args=args), self.assertRaises(ValidationError):
                await get_current_weather.ainvoke(args, CLIENT_CONFIG)
        with self.assertRaises(ValidationError):
            await list_weather_history.ainvoke({"limit": 500}, CLIENT_CONFIG)

    async def test_tools_fail_closed_without_a_client(self) -> None:
        # History must never fall back to an unscoped query.
        with (
            patch("app.ai.tools.get_weather_history", AsyncMock()) as history,
            patch("app.ai.tools.get_weather_for_city", AsyncMock()) as lookup,
        ):
            with self.assertRaises(RuntimeError):
                await list_weather_history.ainvoke({"limit": 3})
            with self.assertRaises(RuntimeError):
                await get_current_weather.ainvoke({"city": "Nairobi"})
        history.assert_not_awaited()
        lookup.assert_not_awaited()


class AskWeatherAssistantTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        # The compiled agent is cached with whatever model was current; tests swap models.
        ai_service._get_agent.cache_clear()
        ai_service._run_slots.cache_clear()
        self.addCleanup(ai_service._get_agent.cache_clear)
        self.addCleanup(ai_service._run_slots.cache_clear)

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

        # Recorded under the calling client (_client uses id=1).
        fetch.assert_awaited_once_with(city="Nairobi", country_code="KE", api_client_id=1)
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

    async def test_model_setup_failure_raises_generic_error(self) -> None:
        # e.g. no API key configured: the provider SDK raises while building the model.
        with (
            patch.object(
                ai_service, "get_chat_model", side_effect=openai.OpenAIError("api_key must be set")
            ),
            self.assertLogs(ai_service.logger, "ERROR"),
            self.assertRaises(AIUpstreamError),
        ):
            await ai_service.ask_weather_assistant("How is Nairobi?", _client(WEATHER_READ))

        self.assertFalse(ai_service._run_slots().locked())

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

    async def test_provider_rate_limit_becomes_retryable_error(self) -> None:
        error = _openai_error(429, headers={"retry-after": "2.5"})
        model = _model(cls=RaisingModel).model_copy(update={"error": error})

        with self.assertLogs(ai_service.logger, "WARNING") as logs, self.assertRaises(AIRateLimitError) as ctx:
            await self._ask(model, WEATHER_READ)

        self.assertEqual(ctx.exception.retry_after, 3)
        self.assertNotIn("Traceback", "\n".join(logs.output))

    async def test_rate_limit_without_header_defaults_to_one_second(self) -> None:
        model = _model(cls=RaisingModel).model_copy(update={"error": _openai_error(429)})

        with self.assertLogs(ai_service.logger, "WARNING"), self.assertRaises(AIRateLimitError) as ctx:
            await self._ask(model, WEATHER_READ)

        self.assertEqual(ctx.exception.retry_after, 1)

    async def test_insufficient_quota_is_not_reported_as_retryable(self) -> None:
        error = _openai_error(429, body={"code": "insufficient_quota", "message": "billing"})
        model = _model(cls=RaisingModel).model_copy(update={"error": error})

        with self.assertLogs(ai_service.logger, "ERROR"), self.assertRaises(AIUpstreamError):
            await self._ask(model, WEATHER_READ)

    async def test_other_provider_status_errors_stay_generic(self) -> None:
        model = _model(cls=RaisingModel).model_copy(update={"error": _openai_error(500)})

        with self.assertLogs(ai_service.logger, "ERROR"), self.assertRaises(AIUpstreamError):
            await self._ask(model, WEATHER_READ)

    async def test_concurrency_cap_rejects_excess_runs_and_releases_slots(self) -> None:
        gate = asyncio.Event()
        held = _model(cls=GateModel).model_copy(update={"gate": gate})

        with patch.object(ai_service.settings, "ai_max_concurrency", 1):
            first = asyncio.create_task(self._ask(held, WEATHER_READ))
            # One loop turn runs the task up to the model call, past taking the only slot.
            await asyncio.sleep(0)
            self.assertTrue(ai_service._run_slots().locked())

            with (
                self.assertLogs(ai_service.logger, "WARNING"),
                self.assertRaises(AIRateLimitError) as ctx,
            ):
                await self._ask(_model(), WEATHER_READ)
            self.assertEqual(ctx.exception.retry_after, 1)

            gate.set()
            self.assertEqual((await first).answer, "ok")
            # The slot is free again once the run finishes.
            self.assertEqual((await self._ask(held, WEATHER_READ)).answer, "ok")

    async def test_slot_is_released_when_a_run_fails(self) -> None:
        with patch.object(ai_service.settings, "ai_max_concurrency", 1):
            with self.assertLogs(ai_service.logger, "ERROR"), self.assertRaises(AIUpstreamError):
                await self._ask(_model(cls=FailingModel), WEATHER_READ)

            self.assertFalse(ai_service._run_slots().locked())

    def test_concurrency_cap_must_be_positive(self) -> None:
        # 0 would reject every request and a negative value breaks asyncio.Semaphore.
        for value in (0, -1):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                Settings(_env_file=None, ai_max_concurrency=value)

    async def test_agent_is_cached_per_tool_scope_set(self) -> None:
        with patch.object(ai_service, "get_chat_model", return_value=_model()) as get_model:
            read_only = ai_service._get_agent(frozenset({WEATHER_READ}))
            self.assertIs(read_only, ai_service._get_agent(frozenset({WEATHER_READ})))
            self.assertIsNot(read_only, ai_service._get_agent(frozenset()))
        self.assertEqual(get_model.call_count, 2)


class ProviderRateLimitTests(unittest.TestCase):
    @staticmethod
    def _retry_after(headers: dict[str, str]) -> int:
        return ai_service._provider_rate_limit(_openai_error(429, headers=headers)).retry_after

    def test_retry_after_forms(self) -> None:
        cases = {
            "seconds": ({"retry-after": "7"}, 7),
            "fractional seconds round up": ({"retry-after": "2.5"}, 3),
            "milliseconds win": ({"retry-after-ms": "1500", "retry-after": "9"}, 2),
            "past date": ({"retry-after": "Wed, 21 Oct 2015 07:28:00 GMT"}, 1),
            "clamped high": ({"retry-after": "3600"}, 60),
            "infinite": ({"retry-after": "inf"}, 60),
            "nan": ({"retry-after": "nan"}, 1),
            "garbage": ({"retry-after": "soon"}, 1),
            "missing": ({}, 1),
        }
        for name, (headers, expected) in cases.items():
            with self.subTest(name):
                self.assertEqual(self._retry_after(headers), expected)

    def test_retry_after_http_date(self) -> None:
        soon = format_datetime(datetime.now(UTC) + timedelta(seconds=30), usegmt=True)
        # The date has whole-second precision, so allow for a second boundary.
        self.assertIn(self._retry_after({"retry-after": soon}), {29, 30})

    def test_cyclic_cause_chain_terminates(self) -> None:
        first, second = RuntimeError("a"), RuntimeError("b")
        first.__cause__, second.__cause__ = second, first
        self.assertIsNone(ai_service._provider_rate_limit(first))


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

    async def test_rate_limit_maps_to_429_with_retry_after(self) -> None:
        with (
            patch.object(
                ai_router,
                "ask_weather_assistant",
                AsyncMock(side_effect=AIRateLimitError("busy", retry_after=7)),
            ),
            self.assertRaises(HTTPException) as ctx,
        ):
            await ai_router.ask(AskRequest(question="hi"), _client(AI_ASK))

        self.assertEqual(ctx.exception.status_code, 429)
        self.assertEqual(ctx.exception.headers, {"Retry-After": "7"})

    def test_blank_question_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            AskRequest(question="   ")
        self.assertEqual(AskRequest(question="  hi ").question, "hi")
