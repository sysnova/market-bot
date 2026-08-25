"""Migration and DBML alignment tests."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS = ROOT / "supabase" / "migrations"
DBML = ROOT / "resources" / "diagrams" / "market_bot.dbml"
TABLES = {
    "alert_analysis_states",
    "alert_continuation_candidates",
    "alert_continuation_sessions",
    "consumer_checkpoints",
    "control_events",
    "entry_opportunities",
    "entry_opportunity_events",
    "engine_decision_states",
    "entry_watch_transitions",
    "entry_watches",
    "long_portfolio_alerts",
    "long_portfolio_states",
    "market_bars",
    "news_intelligence_results",
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
        path.read_text(encoding="utf-8").lower() for path in sorted(MIGRATIONS.glob("*.sql"))
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
        "entry_opportunity_events",
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


@pytest.mark.unit
def test_engine_decision_state_identity_keeps_implementation_rollbacks_independent() -> None:
    sql = all_migration_sql()
    dbml = DBML.read_text(encoding="utf-8").lower()

    assert "unique (engine_name, implementation_version)" in sql
    assert "unique (engine_name)" not in sql
    assert "(engine_name, implementation_version) [unique" in dbml


@pytest.mark.unit
def test_alert_state_is_normalized_and_migrated_from_legacy_checkpoint() -> None:
    sql = all_migration_sql()
    compact_sql = re.sub(r"\s+", " ", sql)
    dbml = DBML.read_text(encoding="utf-8").lower()

    assert "alert_analysis_states_identity_key" in sql
    assert "alert_continuation_candidates_identity_key" in sql
    assert "alert_continuation_sessions_identity_key" in sql
    assert re.search(
        r"horizon in \(\s*'long_term',\s*'dilution',\s*'swing',\s*'intraday',"
        r"\s*'volume_structure',\s*'options_gamma',\s*'news'\s*\)",
        compact_sql,
    )
    assert "gen_random_uuid" not in sql
    assert "alert_analysis_states_identity_key" in dbml
    assert "alert_continuation_candidates_identity_key" in dbml
    assert "alert_continuation_sessions_identity_key" in dbml
    assert (
        "long_term | dilution | swing | intraday | volume_structure | options_gamma | news"
        in dbml
    )


@pytest.mark.unit
def test_market_bar_cache_has_composite_identity_and_runtime_upsert_access() -> None:
    sql = all_migration_sql()

    assert "primary key (symbol, timeframe, timestamp)" in sql
    assert re.search(
        r"grant select, insert, update on market_bot\.market_bars "
        r"to market_bot_runtime",
        sql,
    )
    assert "grant delete on market_bot.market_bars" not in sql
    assert "function market_bot.prune_market_bars" in sql
    assert "grant execute on function market_bot.prune_market_bars" in sql


@pytest.mark.unit
def test_entry_opportunity_retention_is_narrow_and_function_gated() -> None:
    sql = all_migration_sql()
    dbml = DBML.read_text(encoding="utf-8").lower()

    assert "entry_opportunity_events_legacy_evidence_retention_idx" in sql
    assert "entry_opportunity_events_legacy_evidence_retention_idx" in dbml
    assert "function market_bot.prune_entry_opportunity_evidence_events" in sql
    assert "security definer" in sql
    assert "for update of event skip locked" in sql
    assert "limit p_batch_size" in sql
    assert "entry_opportunity_events_maintenance_delete" in sql
    assert "prevent_entry_opportunity_event_mutation" in sql
    for reason in (
        "long_term_evidence_updated",
        "swing_evidence_updated",
        "intraday_evidence_updated",
    ):
        assert reason in sql


@pytest.mark.unit
def test_long_portfolio_state_is_compact_and_updateable() -> None:
    sql = all_migration_sql()

    assert "primary key (rule_version, symbol)" in sql
    assert "qualified_sessions jsonb not null" in sql
    assert re.search(
        r"grant select, insert, update on market_bot\.long_portfolio_states "
        r"to market_bot_runtime",
        sql,
    )
    assert "grant delete on market_bot.long_portfolio_states" not in sql


@pytest.mark.unit
def test_news_intelligence_ledger_supports_deduplication_and_bootstrap() -> None:
    sql = all_migration_sql()

    assert "primary key (provider, article_id)" in sql
    assert re.search(
        r"create index news_intelligence_results_updated_idx\s+"
        r"on market_bot\.news_intelligence_results \(article_updated_at, article_id\)",
        sql,
    )
    assert re.search(
        r"grant select, insert, update on market_bot\.news_intelligence_results\s+"
        r"to market_bot_runtime",
        sql,
    )
    assert "grant delete on market_bot.news_intelligence_results" not in sql
