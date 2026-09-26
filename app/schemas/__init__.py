from app.schemas.ai import ChatCompletionResult
from app.schemas.auth import ApiClientCreated, AuthenticatedClient, TokenResponse
from app.schemas.weather import WeatherHistoryItem, WeatherLocation, WeatherResponse

__all__ = [
    "ApiClientCreated",
    "AuthenticatedClient",
    "ChatCompletionResult",
    "TokenResponse",
    "WeatherHistoryItem",
    "WeatherLocation",
    "WeatherResponse",
]
