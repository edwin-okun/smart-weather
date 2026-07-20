class WeatherServiceError(Exception):
    """Base error for weather service failures."""


class LocationNotFoundError(WeatherServiceError):
    pass


class UpstreamServiceError(WeatherServiceError):
    pass
