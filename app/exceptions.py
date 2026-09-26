class WeatherServiceError(Exception):
    """Base error for weather service failures."""


class LocationNotFoundError(WeatherServiceError):
    pass


class UpstreamServiceError(WeatherServiceError):
    pass


class AIServiceError(Exception):
    """Base error for AI service failures."""


class AIUpstreamError(AIServiceError):
    pass
