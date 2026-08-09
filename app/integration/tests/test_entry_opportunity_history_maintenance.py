from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.integration.entry_opportunity_history_maintenance import (
    DELETE_BATCH_SQL,
    PREVIEW_SQL,
    EntryOpportunityHistoryMaintainer,
    HistoryMaintenanceBatch,
    HistoryMaintenanceStats,
    HistoryRetentionPolicy,
    PostgresEntryOpportunityHistoryStore,
)

CUTOFF = datetime(2026, 7, 1, tzinfo=UTC)


@pytest.mark.unit
async def test_dry_run_reports_candidates_without_deleting() -> None:
    store = AsyncMock()
    store.preview.return_value = HistoryMaintenanceStats(
        candidate_rows=21800,
        candidate_bytes=700_000_000,
        table_total_bytes=748_000_000,
    )
    maintainer = EntryOpportunityHistoryMaintainer(store)
    policy = HistoryRetentionPolicy(cutoff=CUTOFF, retain_per_opportunity=100)

    report = await maintainer.run(policy=policy)

    assert report.applied is False
    assert report.candidate_rows == 21800
    assert report.candidate_bytes == 700_000_000
    assert report.deleted_rows == 0
    store.delete_batch.assert_not_awaited()


@pytest.mark.unit
async def test_apply_deletes_in_short_bounded_batches() -> None:
    store = AsyncMock()
    store.preview.return_value = HistoryMaintenanceStats(
        candidate_rows=2500,
        candidate_bytes=75_000_000,
        table_total_bytes=748_000_000,
    )
    store.delete_batch.side_effect = (
        HistoryMaintenanceBatch(rows=1000, bytes=30_000_000),
        HistoryMaintenanceBatch(rows=1000, bytes=30_000_000),
        HistoryMaintenanceBatch(rows=500, bytes=15_000_000),
    )
    maintainer = EntryOpportunityHistoryMaintainer(store)
    policy = HistoryRetentionPolicy(
        cutoff=CUTOFF,
        retain_per_opportunity=100,
        batch_size=1000,
    )

    report = await maintainer.run(policy=policy, apply=True)

    assert report.applied is True
    assert report.deleted_rows == 2500
    assert report.deleted_bytes == 75_000_000
    assert store.delete_batch.await_count == 3
    assert all(call.args == (policy,) for call in store.delete_batch.await_args_list)


@pytest.mark.unit
def test_sql_only_targets_exact_legacy_evidence_events_and_protects_recent_rows() -> None:
    combined = f"{PREVIEW_SQL}\n{DELETE_BATCH_SQL}"

    for reason in (
        "long_term_evidence_updated",
        "swing_evidence_updated",
        "intraday_evidence_updated",
    ):
        assert reason in combined
    assert "OFFSET :retain_offset" in combined
    assert "e.occurred_at < :cutoff" in combined
    assert "prune_entry_opportunity_evidence_events" in DELETE_BATCH_SQL
    assert ":retain_per_opportunity" in DELETE_BATCH_SQL
    assert ":batch_size" in DELETE_BATCH_SQL


@pytest.mark.integration
async def test_postgres_adapter_opens_one_transaction_per_delete_batch() -> None:
    result = MagicMock()
    result.one.return_value = MagicMock(deleted_rows=2, deleted_bytes=4096)
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    session_context = AsyncMock()
    session_context.__aenter__.return_value = session
    transaction_context = AsyncMock()
    session.begin.return_value = transaction_context
    factory = MagicMock(return_value=session_context)
    store = PostgresEntryOpportunityHistoryStore(factory)  # type: ignore[arg-type]
    policy = HistoryRetentionPolicy(cutoff=CUTOFF, retain_per_opportunity=50)

    batch = await store.delete_batch(policy)

    assert batch == HistoryMaintenanceBatch(rows=2, bytes=4096)
    session.begin.assert_called_once_with()
    session.execute.assert_awaited_once()
