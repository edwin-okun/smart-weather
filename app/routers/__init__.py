from app.routers.ai import router as ai_router
from app.routers.auth import router as auth_router
from app.routers.health import router as health_router
from app.routers.weather import router as weather_router

__all__ = ["ai_router", "auth_router", "health_router", "weather_router"]
