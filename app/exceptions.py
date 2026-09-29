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


class AIRateLimitError(AIServiceError):
    """The provider (or our own concurrency cap) is out of capacity; retry later."""

    def __init__(self, message: str, retry_after: int = 1):
        super().__init__(message)
        self.retry_after = retry_after


class AITimeoutError(AIServiceError):
    pass


class AIStepLimitError(AIServiceError):
    pass
