"""Safe local PostgreSQL bootstrap for the operational intraday paper tracker."""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import LiteralString, cast
from urllib.parse import urlsplit

import psycopg
from psycopg import sql

from app.common.settings import AppSettings


class SchemaState(StrEnum):
    """Observed state of the intraday opportunity relation set."""

    MISSING = "MISSING"
    CURRENT = "CURRENT"


_RELATIONS = (
    "market_bot.intraday_opportunities",
    "market_bot.intraday_fills",
    "market_bot.intraday_opportunity_events",
)
_LOCAL_DATABASE_HOSTS = {None, "localhost", "127.0.0.1", "::1", "host.docker.internal"}


def is_local_database_url(database_url: str) -> bool:
    """Return whether automatic DDL is constrained to a local workstation endpoint."""

    return urlsplit(database_url).hostname in _LOCAL_DATABASE_HOSTS


def classify_schema_state(relations: tuple[object | None, ...]) -> SchemaState:
    """Reject partial DDL instead of guessing how to repair a damaged schema."""

    present = tuple(value is not None for value in relations)
    if all(present):
        return SchemaState.CURRENT
    if not any(present):
        return SchemaState.MISSING
    raise RuntimeError("intraday opportunity migration is partially applied")


def migration_sql(path: Path) -> str:
    """Load and validate the expected versioned local migration artifact."""

    statement = path.read_text(encoding="utf-8")
    if not all(f"create table {relation}" in statement.lower() for relation in _RELATIONS):
        raise RuntimeError("migration does not define all intraday opportunity tables")
    return statement


def ensure_intraday_opportunity_schema(database_url: str, migration_path: Path) -> bool:
    """Apply the migration transactionally only when every owned relation is absent."""

    if not is_local_database_url(database_url):
        raise RuntimeError("refusing to apply an automatic migration to a non-local database")
    connection_url = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(connection_url) as connection, connection.cursor() as cursor:
        cursor.execute(
            "select " + ", ".join("to_regclass(%s)" for _ in _RELATIONS),
            _RELATIONS,
        )
        row = cursor.fetchone()
        if row is None:
            raise RuntimeError("could not inspect intraday opportunity schema")
        state = classify_schema_state(tuple(row))
        if state is SchemaState.CURRENT:
            return False
        trusted_migration = cast(LiteralString, migration_sql(migration_path))
        cursor.execute(sql.SQL(trusted_migration), prepare=False)
    return True


def main() -> None:
    """Prepare the local schema selected by the normal MarketBot settings."""

    settings = AppSettings()
    root = Path(__file__).resolve().parents[2]
    migration_path = (
        root
        / "supabase"
        / "migrations"
        / "20260823233000_intraday_opportunity_lifecycle.sql"
    )
    applied = ensure_intraday_opportunity_schema(
        settings.database_url.get_secret_value(), migration_path
    )
    print(
        json.dumps(
            {
                "migration": migration_path.name,
                "status": "APPLIED" if applied else "CURRENT",
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
