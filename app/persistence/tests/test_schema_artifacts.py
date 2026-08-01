"""Migration and DBML alignment tests."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS = ROOT / "supabase" / "migrations"
DBML = ROOT / "resources" / "diagrams" / "market_bot.dbml"
TABLES = {
    "consumer_checkpoints",
    "control_events",
    "entry_watch_transitions",
    "entry_watches",
    "long_portfolio_alerts",
    "outbox_events",
    "patreon_caps_transitions",
    "patreon_caps_watches",
    "processed_events",
    "rule_versions",
    "run_strategies",
    "runs",
    "service_health",
    "strategy_versions",
}


def migration_sql() -> str:
    matches = list(MIGRATIONS.glob("*_market_bot_foundation.sql"))
    assert len(matches) == 1
    return matches[0].read_text(encoding="utf-8").lower()


def all_migration_sql() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8").lower()
        for path in sorted(MIGRATIONS.glob("*.sql"))
    )


@pytest.mark.unit
def test_migration_creates_only_expected_private_schema_tables() -> None:
    sql = all_migration_sql()
    created = set(re.findall(r"create table market_bot\.([a-z_]+)", sql))

    assert "create schema market_bot" in sql
    assert created == TABLES
    assert "create table public." not in sql
    assert "create table stock." not in sql


@pytest.mark.unit
def test_every_table_has_forced_rls_and_only_runtime_policies() -> None:
    sql = all_migration_sql()

    for table in TABLES:
        assert f"alter table market_bot.{table} enable row level security" in sql
        assert f"alter table market_bot.{table} force row level security" in sql
    assert "to market_bot_runtime" in sql
    assert "to anon" not in sql
    assert "to authenticated" not in sql


@pytest.mark.unit
def test_runtime_role_has_no_delete_and_immutable_tables_have_guard_triggers() -> None:
    sql = all_migration_sql()

    assert "create role market_bot_runtime nologin" in sql
    assert "alter role market_bot_runtime" not in sql
    for unsafe_attribute in {
        "rolcanlogin",
        "rolsuper",
        "rolcreatedb",
        "rolcreaterole",
        "rolreplication",
        "rolbypassrls",
    }:
        assert unsafe_attribute in sql
    assert "existing market_bot_runtime role has unsafe attributes" in sql
    assert "grant market_bot_runtime to postgres" in sql
    assert not re.search(r"grant[^;]*\bdelete\b", sql)
    for table in {
        "control_events",
        "entry_watch_transitions",
        "long_portfolio_alerts",
        "patreon_caps_transitions",
        "processed_events",
        "rule_versions",
        "run_strategies",
        "strategy_versions",
    }:
        assert f"before update or delete on market_bot.{table}" in sql


@pytest.mark.unit
def test_primary_scope_partial_unique_index_includes_strategy_family() -> None:
    sql = migration_sql()

    assert re.search(
        r"create unique index run_strategies_one_primary_per_scope_idx\s+"
        r"on market_bot\.run_strategies \(run_id, engine_id, strategy_family\)\s+"
        r"where mode = 'primary'",
        sql,
    )


@pytest.mark.unit
def test_exact_versions_have_one_identity_regardless_of_content_hash() -> None:
    sql = all_migration_sql()

    assert "unique (engine_id, rule_id, version)" in sql
    assert "unique (engine_id, strategy_id, version)" in sql
    assert "unique (engine_id, rule_id, version, implementation_hash)" not in sql
    assert "unique (engine_id, strategy_id, version, compiled_hash)" not in sql


@pytest.mark.unit
def test_dbml_lists_exactly_the_migration_tables() -> None:
    dbml = DBML.read_text(encoding="utf-8").lower()
    documented = set(re.findall(r"table market_bot\.([a-z_]+)", dbml))

    assert documented == TABLES


@pytest.mark.unit
def test_dbml_documents_exact_version_identity() -> None:
    dbml = DBML.read_text(encoding="utf-8").lower()

    assert "(engine_id, rule_id, version) [unique" in dbml
    assert "(engine_id, strategy_id, version) [unique" in dbml
