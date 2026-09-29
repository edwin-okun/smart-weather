import unittest
from unittest.mock import AsyncMock, patch

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from app.ai.tools import build_tools
from app.permissions import AI_ASK, WEATHER_HISTORY_READ, WEATHER_READ
from app.schemas.auth import AuthenticatedClient
from app.schemas.weather import WeatherLocation, WeatherResponse
from app.services import ai_service


class ToolCallingFakeModel(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def _client(*scopes: str) -> AuthenticatedClient:
    return AuthenticatedClient(id=1, client_id="c", name="c", scopes=set(scopes))


class BuildToolsTests(unittest.TestCase):
    def test_tools_follow_scopes(self) -> None:
        names = lambda s: {t.name for t in build_tools(s)}
        self.assertEqual(names({AI_ASK}), set())
        self.assertEqual(names({WEATHER_READ}), {"get_current_weather"})
        self.assertEqual(
            names({WEATHER_READ, WEATHER_HISTORY_READ}),
            {"get_current_weather", "list_weather_history"},
        )


class AskWeatherAssistantTests(unittest.IsolatedAsyncioTestCase):
    async def test_agent_calls_weather_tool_and_answers(self) -> None:
        model = ToolCallingFakeModel(
            messages=iter(
                [
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
                ]
            )
        )
        weather = WeatherResponse(
            location=WeatherLocation(name="Nairobi", latitude=-1.28, longitude=36.82),
            weather={"current": {"temperature_2m": 21}},
        )
        with (
            patch.object(ai_service, "get_chat_model", return_value=model),
            patch("app.ai.tools.get_weather_for_city", AsyncMock(return_value=weather)) as fetch,
        ):
            result = await ai_service.ask_weather_assistant(
                "How is Nairobi?", _client(WEATHER_READ)
            )

        fetch.assert_awaited_once_with(city="Nairobi", country_code="KE")
        self.assertEqual(result.answer, "It is 21C in Nairobi.")
        self.assertEqual([c.name for c in result.tool_calls], ["get_current_weather"])
        self.assertEqual(result.usage.input_tokens, 30)
        self.assertEqual(result.usage.output_tokens, 11)
