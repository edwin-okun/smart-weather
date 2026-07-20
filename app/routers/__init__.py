from app.routers.health import router as health_router
from app.routers.weather import router as weather_router

__all__ = ["health_router", "weather_router"]
