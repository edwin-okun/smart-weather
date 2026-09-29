import logging

from tortoise import Tortoise
from tortoise.connection import get_connection
from tortoise.context import TortoiseContext
from tortoise.migrations.api import migrate
from tortoise.migrations.executor import MigrationExecutor

from app.config import settings


logger = logging.getLogger(__name__)

# Also the config for Tortoise's migration CLI:
#   uv run python -m tortoise -c app.db.TORTOISE_ORM <command>
TORTOISE_ORM = {
    "connections": {"default": settings.database_url},
    "apps": {
        "models": {
            "models": ["app.models.auth", "app.models.weather"],
            "default_connection": "default",
            "migrations": "app.migrations",
        },
    },
}

_db_context: TortoiseContext | None = None


async def run_migrations() -> None:
    """Apply all pending migrations, in a Tortoise context of their own."""
    async with TortoiseContext():
        await migrate(config=TORTOISE_ORM)


async def _pending_migrations() -> list[str]:
    """Names of migrations not yet recorded as applied, per app connection."""
    pending: list[str] = []
    for label, app_config in TORTOISE_ORM["apps"].items():
        connection = get_connection(app_config["default_connection"])
        executor = MigrationExecutor(connection, {label: app_config})
        pending += [
            f"{step.migration.app_label}.{step.migration.name}"
            for step in await executor.plan()
            if not step.backward
        ]
    return pending


async def init_db() -> None:
    global _db_context

    if settings.run_db_migrations_on_startup:
        await run_migrations()
    _db_context = await Tortoise.init(config=TORTOISE_ORM, _enable_global_fallback=True)
    if settings.generate_db_schemas:
        # Creates missing tables only and never alters existing ones; meant for
        # throwaway databases (tests). Real databases are managed by migrations.
        await _db_context.generate_schemas()
    else:
        pending = await _pending_migrations()
        if pending:
            logger.warning(
                "Database has unapplied migrations (%s); run `python -m app.cli migrate`.",
                ", ".join(pending),
            )


async def close_db() -> None:
    global _db_context

    if _db_context is not None:
        await _db_context.close_connections()
        _db_context = None
