from pathlib import Path

import pytest

from app.integration.local_schema_bootstrap import (
    SchemaState,
    classify_schema_state,
    is_local_database_url,
    migration_sql,
)


def test_local_database_url_accepts_wsl_to_windows_local_endpoints() -> None:
    assert is_local_database_url("postgresql://marketbot:secret@localhost:5432/marketbot")
    assert is_local_database_url("postgresql://marketbot:secret@127.0.0.1:5432/marketbot")
    assert is_local_database_url(
        "postgresql://marketbot:secret@host.docker.internal:5432/marketbot"
    )
    assert not is_local_database_url(
        "postgresql://marketbot:secret@database.example.com:5432/marketbot"
    )


def test_schema_state_requires_all_intraday_relations_or_none() -> None:
    assert classify_schema_state((None, None, None)) is SchemaState.MISSING
    assert classify_schema_state(("a", "b", "c")) is SchemaState.CURRENT

    with pytest.raises(RuntimeError, match="partially applied"):
        classify_schema_state(("a", None, "c"))


def test_migration_loader_rejects_unexpected_artifact(tmp_path: Path) -> None:
    migration = tmp_path / "migration.sql"
    migration.write_text("select 1;", encoding="utf-8")

    with pytest.raises(RuntimeError, match="intraday opportunity tables"):
        migration_sql(migration)
