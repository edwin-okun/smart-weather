from tortoise import Tortoise
from tortoise.context import TortoiseContext

from app.config import settings


TORTOISE_ORM = {
    "connections": {"default": settings.database_url},
    "apps": {
        "models": {
            "models": ["app.models.weather"],
            "default_connection": "default",
        },
    },
}

_db_context: TortoiseContext | None = None


async def init_db() -> None:
    global _db_context

    _db_context = await Tortoise.init(config=TORTOISE_ORM, _enable_global_fallback=True)
    if settings.generate_db_schemas:
        await _db_context.generate_schemas()


async def close_db() -> None:
    global _db_context

    if _db_context is not None:
        await _db_context.close_connections()
        _db_context = None
