"""Migrations in app/migrations must build exactly the schema the models describe.

The app tests create their tables with generate_schemas(), so without these
checks a model change without a migration (or a migration that builds the wrong
schema) would go unnoticed until a real database was upgraded.
"""

import asyncio
import copy
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from tortoise.context import TortoiseContext
from tortoise.migrations.api import migrate
from tortoise.migrations.autodetector import MigrationAutodetector

from app import db
from app.config import settings
from app.main import app


BASELINE = "models.0001_initial"


def _config(path: Path) -> dict:
    config = copy.deepcopy(db.TORTOISE_ORM)
    config["connections"]["default"] = f"sqlite://{path}"
    return config


async def _migrate(path: Path, target: str | None = None) -> None:
    async with TortoiseContext():
        await migrate(config=_config(path), target=target)


async def _generate_schemas(path: Path) -> None:
    async with TortoiseContext() as ctx:
        await ctx.init(config=_config(path))
        await ctx.generate_schemas()


def _schema(path: Path) -> dict[str, set[str]]:
    """Columns, foreign keys and indexes per table; column order is ignored."""
    con = sqlite3.connect(path)
    try:
        schema: dict[str, set[str]] = {}
        tables = con.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%' AND name != 'tortoise_migrations'"
        ).fetchall()
        for (table,) in tables:
            items = {
                f"column {name} {type_} notnull={notnull} pk={pk}"
                for _, name, type_, notnull, _, pk in con.execute(
                    f'PRAGMA table_info("{table}")'
                )
            }
            items |= {
                f"fk {row[3]} -> {row[2]}.{row[4]} on_delete={row[6]}"
                for row in con.execute(f'PRAGMA foreign_key_list("{table}")')
            }
            for _, index, unique, _, _ in con.execute(f'PRAGMA index_list("{table}")'):
                columns = [row[2] for row in con.execute(f'PRAGMA index_info("{index}")')]
                name = "(auto)" if index.startswith("sqlite_autoindex") else index
                items.add(f"index {name} unique={unique} columns={columns}")
            schema[table] = items
        return schema
    finally:
        con.close()


class MigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def test_migrations_build_the_model_schema(self) -> None:
        migrated, generated = self.dir / "migrated.sqlite3", self.dir / "generated.sqlite3"
        asyncio.run(_migrate(migrated))
        asyncio.run(_generate_schemas(generated))

        schema = _schema(migrated)
        self.assertIn("column family_id VARCHAR(64) notnull=0 pk=0", schema["access_tokens"])
        self.assertIn("column client_id INT notnull=0 pk=0", schema["weather_lookups"])
        self.assertIn(
            "fk client_id -> api_clients.id on_delete=CASCADE", schema["weather_lookups"]
        )
        indexed = {
            item.split("columns=")[1]
            for items in schema.values()
            for item in items
            if item.startswith("index")
        }
        self.assertIn("['family_id']", indexed)
        self.assertIn("['client_id', 'created_at']", indexed)
        self.assertIn("['created_at']", indexed)

        self.assertEqual(schema, _schema(generated))

    def test_models_have_no_changes_missing_a_migration(self) -> None:
        async def detect() -> list[str]:
            config = _config(self.dir / "unused.sqlite3")
            async with TortoiseContext() as ctx:
                await ctx.init(config=config)
                writers = await MigrationAutodetector(ctx.apps, config["apps"]).changes()
            return [f"{writer.app_label}.{writer.name}" for writer in writers]

        self.assertEqual(
            asyncio.run(detect()),
            [],
            "models changed without a migration; run makemigrations (see README)",
        )

    def test_upgrade_from_baseline_keeps_rows_and_downgrade_restores_it(self) -> None:
        path = self.dir / "upgrade.sqlite3"
        asyncio.run(_migrate(path, target=BASELINE))
        baseline = _schema(path)
        con = sqlite3.connect(path)
        with con:
            con.execute(
                "INSERT INTO api_clients (client_id, client_secret_hash, name, scopes, "
                "status, created_at, updated_at) VALUES ('c1', 'h', 'n', '[]', 'active', "
                "'2026-01-01', '2026-01-01')"
            )
            con.execute(
                "INSERT INTO access_tokens (token_hash, scopes, expires_at, created_at, "
                "client_id) VALUES ('t1', '[]', '2026-01-02', '2026-01-01', 1)"
            )
            con.execute(
                "INSERT INTO weather_lookups (city, country_code, location_name, latitude, "
                "longitude, weather, created_at) VALUES ('Nairobi', 'KE', 'Nairobi', 0, 0, "
                "'{}', '2026-01-01')"
            )
        con.close()

        asyncio.run(_migrate(path))
        con = sqlite3.connect(path)
        self.assertEqual(
            con.execute("SELECT token_hash, family_id FROM access_tokens").fetchall(),
            [("t1", None)],
        )
        self.assertEqual(
            con.execute("SELECT city, client_id FROM weather_lookups").fetchall(),
            [("Nairobi", None)],
        )
        con.close()

        asyncio.run(_migrate(path, target=BASELINE))
        self.assertEqual(_schema(path), baseline)
        con = sqlite3.connect(path)
        self.assertEqual(con.execute("SELECT count(*) FROM weather_lookups").fetchone(), (1,))
        con.close()


class StartupMigrationCheckTests(unittest.TestCase):
    """Without schema generation, the app must not start on an unmigrated database."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "app.sqlite3"
        for patcher in (
            patch.dict(db.TORTOISE_ORM["connections"], {"default": f"sqlite://{self.path}"}),
            patch.object(settings, "generate_db_schemas", False),
            patch.object(settings, "run_db_migrations_on_startup", False),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_startup_fails_while_migrations_are_pending(self) -> None:
        for target, pending in [
            (None, ["models.0001_initial", "models.0002_token_family_and_client_history"]),
            (BASELINE, ["models.0002_token_family_and_client_history"]),
        ]:
            with self.subTest(migrated_to=target):
                if target is not None:
                    asyncio.run(_migrate(self.path, target=target))
                with self.assertRaises(db.PendingMigrationsError) as raised:
                    with TestClient(app):
                        pass
                self.assertEqual(raised.exception.pending, pending)
                self.assertIn("smart-weather migrate", str(raised.exception))

    def test_startup_serves_once_migrated(self) -> None:
        asyncio.run(_migrate(self.path))
        with TestClient(app) as client:
            self.assertEqual(client.get("/health").status_code, 200)
            response = client.post(
                "/register", json={"redirect_uris": ["https://client.example/callback"]}
            )
        self.assertEqual(response.status_code, 201, response.text)

    def test_startup_can_apply_migrations_itself(self) -> None:
        with patch.object(settings, "run_db_migrations_on_startup", True):
            with TestClient(app) as client:
                self.assertEqual(client.get("/health").status_code, 200)


if __name__ == "__main__":
    unittest.main()
