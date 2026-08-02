"""ORM mapping tests that need no live PostgreSQL server."""

from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import UniqueConstraint

from app.persistence.models import (
    Base,
    EntryWatchRecord,
    RuleVersion,
    Run,
    RunStrategy,
    StrategyVersion,
    new_entity_id,
)

EXPECTED_TABLES = {
    "consumer_checkpoints",
    "control_events",
    "entry_watch_transitions",
    "entry_watches",
    "long_portfolio_alerts",
    "long_portfolio_states",
    "market_bars",
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


@pytest.mark.unit
def test_all_models_are_mapped_to_private_market_bot_schema() -> None:
    assert set(Base.metadata.tables) == {f"market_bot.{name}" for name in EXPECTED_TABLES}


@pytest.mark.unit
def test_entity_ids_are_uuid7_created_by_python() -> None:
    entity_id = new_entity_id()

    assert isinstance(entity_id, UUID)
    assert entity_id.version == 7
    assert Run.__table__.c.id.server_default is None


@pytest.mark.unit
def test_primary_strategy_uniqueness_is_a_partial_database_index() -> None:
    matching = [
        index
        for index in RunStrategy.__table__.indexes
        if index.name == "run_strategies_one_primary_per_scope_idx"
    ]

    assert len(matching) == 1
    index = matching[0]
    assert index.unique is True
    assert [column.name for column in index.columns] == [
        "run_id",
        "engine_id",
        "strategy_family",
    ]
    assert str(index.dialect_options["postgresql"]["where"]) == "mode = 'PRIMARY'"


@pytest.mark.unit
def test_run_strategy_mapping_includes_foreign_key_lookup_index() -> None:
    index_names = {index.name for index in RunStrategy.__table__.indexes}

    assert "run_strategies_strategy_version_id_idx" in index_names


@pytest.mark.unit
def test_entry_watch_has_one_active_thesis_per_symbol() -> None:
    matching = [
        index
        for index in EntryWatchRecord.__table__.indexes
        if index.name == "entry_watches_one_active_per_symbol_idx"
    ]

    assert len(matching) == 1
    assert matching[0].unique is True
    assert str(matching[0].dialect_options["postgresql"]["where"]) == (
        "status IN ('ARMED', 'IN_ZONE')"
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("model", "expected_columns"),
    [
        (RuleVersion, ["engine_id", "rule_id", "version"]),
        (StrategyVersion, ["engine_id", "strategy_id", "version"]),
    ],
)
def test_version_identity_does_not_include_content_hash(
    model: type[RuleVersion] | type[StrategyVersion],
    expected_columns: list[str],
) -> None:
    identities = [
        constraint
        for constraint in model.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
        and constraint.name is not None
        and constraint.name.endswith("_identity_key")
    ]

    assert len(identities) == 1
    assert [column.name for column in identities[0].columns] == expected_columns
