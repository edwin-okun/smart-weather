from app.schemas.ai import AskRequest, AskResponse
from app.schemas.auth import ApiClientCreated, AuthenticatedClient, TokenResponse
from app.schemas.weather import WeatherHistoryItem, WeatherLocation, WeatherResponse

__all__ = [
    "ApiClientCreated",
    "AuthenticatedClient",
    "AskRequest",
    "AskResponse",
    "TokenResponse",
    "WeatherHistoryItem",
    "WeatherLocation",
    "WeatherResponse",
]
