"""Safe bounded retention for legacy Entry Opportunity evidence snapshots."""

# ruff: noqa: S608 -- all SQL interpolation below combines developer-owned constants only.

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.common.settings import AppSettings, Environment
from app.persistence import create_database_engine, create_session_factory

_LEGACY_EVIDENCE_FILTER = """
e.reasons IN (
    '["long_term_evidence_updated"]'::jsonb,
    '["swing_evidence_updated"]'::jsonb,
    '["intraday_evidence_updated"]'::jsonb
)
""".strip()

_CANDIDATE_CTES = f"""
WITH retention_boundaries AS MATERIALIZED (
    SELECT
        opportunity.id AS opportunity_id,
        boundary.occurred_at AS protected_occurred_at,
        boundary.id AS protected_event_id
    FROM market_bot.entry_opportunities AS opportunity
    CROSS JOIN LATERAL (
        SELECT event.occurred_at, event.id
        FROM market_bot.entry_opportunity_events AS event
        WHERE event.opportunity_id = opportunity.id
        ORDER BY event.occurred_at DESC, event.id DESC
        OFFSET :retain_offset
        LIMIT 1
    ) AS boundary
),
candidates AS MATERIALIZED (
    SELECT e.id, e.occurred_at, pg_column_size(e) AS row_bytes
    FROM retention_boundaries AS boundary
    JOIN market_bot.entry_opportunity_events AS e
      ON e.opportunity_id = boundary.opportunity_id
     AND (e.occurred_at, e.id) < (
         boundary.protected_occurred_at,
         boundary.protected_event_id
     )
    WHERE e.occurred_at < :cutoff
      AND {_LEGACY_EVIDENCE_FILTER}
)
"""

PREVIEW_SQL = f"""
{_CANDIDATE_CTES}
SELECT
    count(*)::bigint AS candidate_rows,
    coalesce(sum(row_bytes), 0)::bigint AS candidate_bytes,
    pg_total_relation_size(
        'market_bot.entry_opportunity_events'::regclass
    )::bigint AS table_total_bytes
FROM candidates
"""

DELETE_BATCH_SQL = """
SELECT
    deleted_rows,
    deleted_bytes
FROM market_bot.prune_entry_opportunity_evidence_events(
    :cutoff,
    :retain_per_opportunity,
    :batch_size
)
"""


@dataclass(frozen=True, slots=True)
class HistoryRetentionPolicy:
    cutoff: datetime
    retain_per_opportunity: int = 100
    batch_size: int = 1000

    def __post_init__(self) -> None:
        if self.cutoff.tzinfo is None or self.cutoff.utcoffset() != timedelta(0):
            raise ValueError("cutoff must be timezone-aware UTC")
        if self.retain_per_opportunity < 1:
            raise ValueError("retain_per_opportunity must be positive")
        if not 1 <= self.batch_size <= 10_000:
            raise ValueError("batch_size must be between 1 and 10000")

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "cutoff": self.cutoff,
            "retain_offset": self.retain_per_opportunity - 1,
            "retain_per_opportunity": self.retain_per_opportunity,
            "batch_size": self.batch_size,
        }


@dataclass(frozen=True, slots=True)
class HistoryMaintenanceStats:
    candidate_rows: int
    candidate_bytes: int
    table_total_bytes: int


@dataclass(frozen=True, slots=True)
class HistoryMaintenanceBatch:
    rows: int
    bytes: int


@dataclass(frozen=True, slots=True)
class HistoryMaintenanceReport:
    applied: bool
    cutoff: datetime
    retain_per_opportunity: int
    batch_size: int
    candidate_rows: int
    candidate_bytes: int
    table_total_bytes: int
    deleted_rows: int
    deleted_bytes: int

    def as_dict(self) -> dict[str, object]:
        values = asdict(self)
        values["cutoff"] = self.cutoff.isoformat()
        values["scope"] = "legacy_single_reason_evidence_updated_only"
        values["space_reclamation_required"] = "VACUUM_FULL_or_pg_repack"
        return values


class EntryOpportunityHistoryStore(Protocol):
    async def preview(self, policy: HistoryRetentionPolicy) -> HistoryMaintenanceStats: ...

    async def delete_batch(
        self, policy: HistoryRetentionPolicy
    ) -> HistoryMaintenanceBatch: ...


class EntryOpportunityHistoryMaintainer:
    def __init__(self, store: EntryOpportunityHistoryStore) -> None:
        self._store = store

    async def run(
        self,
        *,
        policy: HistoryRetentionPolicy,
        apply: bool = False,
    ) -> HistoryMaintenanceReport:
        stats = await self._store.preview(policy)
        deleted_rows = 0
        deleted_bytes = 0
        if apply:
            while deleted_rows < stats.candidate_rows:
                batch = await self._store.delete_batch(policy)
                deleted_rows += batch.rows
                deleted_bytes += batch.bytes
                if batch.rows == 0 or batch.rows < policy.batch_size:
                    break
        return HistoryMaintenanceReport(
            applied=apply,
            cutoff=policy.cutoff,
            retain_per_opportunity=policy.retain_per_opportunity,
            batch_size=policy.batch_size,
            candidate_rows=stats.candidate_rows,
            candidate_bytes=stats.candidate_bytes,
            table_total_bytes=stats.table_total_bytes,
            deleted_rows=deleted_rows,
            deleted_bytes=deleted_bytes,
        )


class PostgresEntryOpportunityHistoryStore:
    """Use one short PostgreSQL transaction for preview or each delete batch."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def preview(self, policy: HistoryRetentionPolicy) -> HistoryMaintenanceStats:
        async with self._session_factory() as session, session.begin():
            result = await session.execute(text(PREVIEW_SQL), policy.parameters)
            row = result.one()
        return HistoryMaintenanceStats(
            candidate_rows=row.candidate_rows,
            candidate_bytes=row.candidate_bytes,
            table_total_bytes=row.table_total_bytes,
        )

    async def delete_batch(
        self, policy: HistoryRetentionPolicy
    ) -> HistoryMaintenanceBatch:
        async with self._session_factory() as session, session.begin():
            result = await session.execute(text(DELETE_BATCH_SQL), policy.parameters)
            row = result.one()
        return HistoryMaintenanceBatch(rows=row.deleted_rows, bytes=row.deleted_bytes)


async def maintain_entry_opportunity_history(
    *,
    cutoff: datetime,
    retain_per_opportunity: int = 100,
    batch_size: int = 1000,
    apply: bool = False,
) -> dict[str, object]:
    """Compose local PostgreSQL maintenance; dry-run unless apply is explicitly true."""

    settings = AppSettings()
    database = create_database_engine(
        settings.database_url.get_secret_value(),
        require_ssl=settings.environment is Environment.PRODUCTION,
    )
    try:
        store = PostgresEntryOpportunityHistoryStore(create_session_factory(database))
        report = await EntryOpportunityHistoryMaintainer(store).run(
            policy=HistoryRetentionPolicy(
                cutoff=cutoff,
                retain_per_opportunity=retain_per_opportunity,
                batch_size=batch_size,
            ),
            apply=apply,
        )
        return report.as_dict()
    finally:
        await database.dispose()
