"""SQLAlchemy mappings for the private ``market_bot`` PostgreSQL schema."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.contracts import new_uuid7

SCHEMA = "market_bot"
SHA256_PATTERN = r"^sha256:[0-9a-f]{64}$"


def utc_now() -> datetime:
    """Return a timezone-aware timestamp for application-side defaults."""

    return datetime.now(UTC)


def new_entity_id() -> UUID:
    """Create the time-ordered identifier required by every persisted entity."""

    return new_uuid7()


class Base(DeclarativeBase):
    """Declarative base with deterministic constraint names."""

    metadata = MetaData(
        naming_convention={
            "ix": "%(table_name)s_%(column_0_name)s_idx",
            "uq": "%(table_name)s_%(column_0_name)s_key",
            "ck": "%(table_name)s_%(constraint_name)s_check",
            "fk": "%(table_name)s_%(column_0_name)s_fkey",
            "pk": "%(table_name)s_pkey",
        }
    )


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (
        CheckConstraint(
            "status in ('CREATED', 'RUNNING', 'COMPLETED', 'FAILED', 'CANCELLED')",
            name="status",
        ),
        CheckConstraint(
            "completed_at is null or completed_at >= started_at",
            name="completed_after_start",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=new_entity_id)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class RuleVersion(Base):
    __tablename__ = "rule_versions"
    __table_args__ = (
        UniqueConstraint(
            "engine_id",
            "rule_id",
            "version",
            name="rule_versions_identity_key",
        ),
        CheckConstraint(
            f"implementation_hash ~ '{SHA256_PATTERN}'", name="implementation_hash_format"
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=new_entity_id)
    rule_id: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(Text, nullable=False)
    family: Mapped[str] = mapped_column(Text, nullable=False)
    engine_id: Mapped[str] = mapped_column(Text, nullable=False)
    implementation_hash: Mapped[str] = mapped_column(Text, nullable=False)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class StrategyVersion(Base):
    __tablename__ = "strategy_versions"
    __table_args__ = (
        UniqueConstraint(
            "engine_id",
            "strategy_id",
            "version",
            name="strategy_versions_identity_key",
        ),
        CheckConstraint(f"compiled_hash ~ '{SHA256_PATTERN}'", name="compiled_hash_format"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=new_entity_id)
    strategy_id: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(Text, nullable=False)
    family: Mapped[str] = mapped_column(Text, nullable=False)
    engine_id: Mapped[str] = mapped_column(Text, nullable=False)
    compiled_hash: Mapped[str] = mapped_column(Text, nullable=False)
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class RunStrategy(Base):
    __tablename__ = "run_strategies"
    __table_args__ = (
        UniqueConstraint("run_id", "strategy_version_id", name="run_strategies_assignment_key"),
        CheckConstraint("mode in ('PRIMARY', 'CANDIDATE', 'RESEARCH', 'DISABLED')", name="mode"),
        Index(
            "run_strategies_one_primary_per_scope_idx",
            "run_id",
            "engine_id",
            "strategy_family",
            unique=True,
            postgresql_where=text("mode = 'PRIMARY'"),
        ),
        Index("run_strategies_strategy_version_id_idx", "strategy_version_id"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=new_entity_id)
    run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.runs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    strategy_version_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.strategy_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    engine_id: Mapped[str] = mapped_column(Text, nullable=False)
    strategy_family: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class ProcessedEvent(Base):
    __tablename__ = "processed_events"
    __table_args__ = (
        UniqueConstraint("consumer_name", "event_id", name="processed_events_delivery_key"),
        CheckConstraint(f"payload_hash ~ '{SHA256_PATTERN}'", name="payload_hash_format"),
        Index("processed_events_run_id_idx", "run_id"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=new_entity_id)
    consumer_name: Mapped[str] = mapped_column(Text, nullable=False)
    event_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    run_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey(f"{SCHEMA}.runs.id", ondelete="RESTRICT")
    )
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    payload_hash: Mapped[str] = mapped_column(Text, nullable=False)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class OutboxEvent(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        CheckConstraint("attempts >= 0", name="attempts_nonnegative"),
        CheckConstraint(
            "published_at is null or published_at >= occurred_at",
            name="published_order",
        ),
        Index(
            "outbox_events_pending_idx",
            "available_at",
            "created_at",
            postgresql_where=text("published_at IS NULL"),
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=new_entity_id)
    aggregate_type: Mapped[str] = mapped_column(Text, nullable=False)
    aggregate_id: Mapped[str] = mapped_column(Text, nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    headers: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class ConsumerCheckpoint(Base):
    __tablename__ = "consumer_checkpoints"
    __table_args__ = (
        UniqueConstraint("consumer_name", "stream", name="consumer_checkpoints_position_key"),
        CheckConstraint("sequence >= 0", name="sequence_nonnegative"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=new_entity_id)
    consumer_name: Mapped[str] = mapped_column(Text, nullable=False)
    stream: Mapped[str] = mapped_column(Text, nullable=False)
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class EngineDecisionStateRecord(Base):
    __tablename__ = "engine_decision_states"
    __table_args__ = (
        UniqueConstraint(
            "engine_name",
            "implementation_version",
            name="engine_decision_states_engine_implementation_key",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=new_entity_id)
    engine_name: Mapped[str] = mapped_column(Text, nullable=False)
    implementation_version: Mapped[str] = mapped_column(Text, nullable=False)
    state_schema_version: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class AlertAnalysisStateRecord(Base):
    __tablename__ = "alert_analysis_states"
    __table_args__ = (
        CheckConstraint(
            "horizon in ('LONG_TERM', 'DILUTION', 'SWING', 'INTRADAY', 'VOLUME_STRUCTURE')",
            name="horizon",
        ),
        UniqueConstraint(
            "engine_name",
            "implementation_version",
            "symbol",
            "horizon",
            name="alert_analysis_states_identity_key",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=new_entity_id)
    engine_name: Mapped[str] = mapped_column(Text, nullable=False)
    implementation_version: Mapped[str] = mapped_column(Text, nullable=False)
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    horizon: Mapped[str] = mapped_column(Text, nullable=False)
    analysis_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class AlertContinuationCandidateRecord(Base):
    __tablename__ = "alert_continuation_candidates"
    __table_args__ = (
        UniqueConstraint(
            "engine_name",
            "implementation_version",
            "symbol",
            name="alert_continuation_candidates_identity_key",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=new_entity_id)
    engine_name: Mapped[str] = mapped_column(Text, nullable=False)
    implementation_version: Mapped[str] = mapped_column(Text, nullable=False)
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class AlertContinuationSessionRecord(Base):
    __tablename__ = "alert_continuation_sessions"
    __table_args__ = (
        UniqueConstraint(
            "engine_name",
            "implementation_version",
            "symbol",
            name="alert_continuation_sessions_identity_key",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=new_entity_id)
    engine_name: Mapped[str] = mapped_column(Text, nullable=False)
    implementation_version: Mapped[str] = mapped_column(Text, nullable=False)
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    market_session: Mapped[date] = mapped_column(Date, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class ServiceHealthRecord(Base):
    __tablename__ = "service_health"
    __table_args__ = (
        UniqueConstraint("service_name", name="service_health_service_name_key"),
        CheckConstraint("status in ('HEALTHY', 'DEGRADED', 'UNHEALTHY', 'UNKNOWN')", name="status"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=new_entity_id)
    service_name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class ControlEvent(Base):
    __tablename__ = "control_events"
    __table_args__ = (
        Index("control_events_run_id_created_at_idx", "run_id", "created_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=new_entity_id)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    run_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey(f"{SCHEMA}.runs.id", ondelete="RESTRICT")
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class EntryWatchRecord(Base):
    __tablename__ = "entry_watches"
    __table_args__ = (
        CheckConstraint(
            "status in ('ARMED', 'IN_ZONE', 'EARLY_ENTRY', 'IMPULSE_EXTENDED', "
            "'TRIGGERED', 'INVALIDATED', 'EXPIRED')",
            name="status",
        ),
        CheckConstraint("invalidation < zone_low and zone_low <= zone_high", name="level_order"),
        CheckConstraint("correction_target_percent >= 0", name="correction_nonnegative"),
        CheckConstraint("expires_at > armed_at", name="expiry_after_arm"),
        CheckConstraint(f"source_context_hash ~ '{SHA256_PATTERN}'", name="context_hash_format"),
        Index(
            "entry_watches_one_active_per_symbol_idx",
            "symbol",
            unique=True,
            postgresql_where=text(
                "status IN ('ARMED', 'IN_ZONE', 'EARLY_ENTRY', 'IMPULSE_EXTENDED')"
            ),
        ),
        Index("entry_watches_status_expires_at_idx", "status", "expires_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    thesis_version: Mapped[str] = mapped_column(Text, nullable=False)
    armed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    terminal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    zone_low: Mapped[Decimal] = mapped_column(Numeric(28, 8), nullable=False)
    zone_high: Mapped[Decimal] = mapped_column(Numeric(28, 8), nullable=False)
    invalidation: Mapped[Decimal] = mapped_column(Numeric(28, 8), nullable=False)
    original_price: Mapped[Decimal] = mapped_column(Numeric(28, 8), nullable=False)
    current_price: Mapped[Decimal] = mapped_column(Numeric(28, 8), nullable=False)
    correction_target_percent: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    source_analysis_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    source_context_hash: Mapped[str] = mapped_column(Text, nullable=False)
    anchor_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class EntryWatchTransitionRecord(Base):
    __tablename__ = "entry_watch_transitions"
    __table_args__ = (
        CheckConstraint(
            "status in ('ARMED', 'IN_ZONE', 'EARLY_ENTRY', 'IMPULSE_EXTENDED', "
            "'TRIGGERED', 'INVALIDATED', 'EXPIRED')",
            name="status",
        ),
        CheckConstraint(
            "previous_status is null or previous_status in "
            "('ARMED', 'IN_ZONE', 'EARLY_ENTRY', 'IMPULSE_EXTENDED', "
            "'TRIGGERED', 'INVALIDATED', 'EXPIRED')",
            name="previous_status",
        ),
        Index("entry_watch_transitions_watch_occurred_idx", "watch_id", "occurred_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    watch_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.entry_watches.id", ondelete="RESTRICT"),
        nullable=False,
    )
    previous_status: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    current_price: Mapped[Decimal] = mapped_column(Numeric(28, 8), nullable=False)
    reasons: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    horizons: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    source_analysis_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class EntryOpportunityRecord(Base):
    """Current materialized paper-opportunity state for one ticker thesis."""

    __tablename__ = "entry_opportunities"
    __table_args__ = (
        CheckConstraint(
            "status in ('ARMED', 'IN_ZONE', 'CONFIRMING', 'OPEN', 'CLOSED')",
            name="status",
        ),
        CheckConstraint(
            "current_maturity in ('ARMED', 'IN_ZONE', 'L1', 'L2', 'L3', 'L4')",
            name="current_maturity",
        ),
        CheckConstraint(
            "peak_maturity in ('ARMED', 'IN_ZONE', 'L1', 'L2', 'L3', 'L4')",
            name="peak_maturity",
        ),
        CheckConstraint("progress_percent between 0 and 100", name="progress"),
        CheckConstraint("invalidation < zone_low and zone_low <= zone_high", name="levels"),
        CheckConstraint("expires_at > armed_at", name="expiry"),
        CheckConstraint("revision >= 1", name="revision_positive"),
        CheckConstraint(
            "(status = 'CLOSED') = (closed_at is not null and close_reason is not null)",
            name="closure_evidence",
        ),
        Index(
            "entry_opportunities_one_active_per_symbol_idx",
            "symbol",
            unique=True,
            postgresql_where=text("status <> 'CLOSED'"),
        ),
        Index("entry_opportunities_status_expires_idx", "status", "expires_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    current_maturity: Mapped[str] = mapped_column(Text, nullable=False)
    peak_maturity: Mapped[str] = mapped_column(Text, nullable=False)
    progress_percent: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    original_watch_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.entry_watches.id", ondelete="RESTRICT"),
    )
    armed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    close_reason: Mapped[str | None] = mapped_column(Text)
    zone_low: Mapped[Decimal] = mapped_column(Numeric(28, 8), nullable=False)
    zone_high: Mapped[Decimal] = mapped_column(Numeric(28, 8), nullable=False)
    invalidation: Mapped[Decimal] = mapped_column(Numeric(28, 8), nullable=False)
    original_price: Mapped[Decimal] = mapped_column(Numeric(28, 8), nullable=False)
    current_price: Mapped[Decimal] = mapped_column(Numeric(28, 8), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class EntryOpportunityEventRecord(Base):
    """Immutable lifecycle evidence and complete state snapshot."""

    __tablename__ = "entry_opportunity_events"
    __table_args__ = (
        Index(
            "entry_opportunity_events_opportunity_occurred_idx",
            "opportunity_id",
            "occurred_at",
        ),
        Index("entry_opportunity_events_symbol_occurred_idx", "symbol", "occurred_at"),
        Index(
            "entry_opportunity_events_legacy_evidence_retention_idx",
            "opportunity_id",
            "occurred_at",
            "id",
            postgresql_where=text(
                "reasons in ('[\"long_term_evidence_updated\"]'::jsonb, "
                "'[\"swing_evidence_updated\"]'::jsonb, "
                "'[\"intraday_evidence_updated\"]'::jsonb)"
            ),
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    opportunity_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.entry_opportunities.id", ondelete="RESTRICT"),
        nullable=False,
    )
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reasons: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class LongPortfolioAlertRecord(Base):
    __tablename__ = "long_portfolio_alerts"
    __table_args__ = (
        UniqueConstraint("deduplication_key", name="long_portfolio_alerts_deduplication_key_key"),
        CheckConstraint(
            "invalidation < buy_zone_low and buy_zone_low <= buy_zone_high",
            name="levels",
        ),
        CheckConstraint(
            "target_weight_percent > 0 and target_weight_percent <= 100", name="weight"
        ),
        CheckConstraint("tranche_percent > 0 and tranche_percent <= 100", name="tranche"),
        CheckConstraint("target_capital_usd > 0 and tranche_usd > 0", name="money"),
        CheckConstraint("score >= 0 and score <= 100", name="score"),
        Index("long_portfolio_alerts_symbol_created_idx", "symbol", "created_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    deduplication_key: Mapped[str] = mapped_column(Text, nullable=False)
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rule_version: Mapped[str] = mapped_column(Text, nullable=False)
    horizon_end: Mapped[date] = mapped_column(Date, nullable=False)
    current_price: Mapped[Decimal] = mapped_column(Numeric(28, 8), nullable=False)
    buy_zone_low: Mapped[Decimal] = mapped_column(Numeric(28, 8), nullable=False)
    buy_zone_high: Mapped[Decimal] = mapped_column(Numeric(28, 8), nullable=False)
    invalidation: Mapped[Decimal] = mapped_column(Numeric(28, 8), nullable=False)
    target_weight_percent: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    target_capital_usd: Mapped[Decimal] = mapped_column(Numeric(28, 2), nullable=False)
    tranche_percent: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    tranche_usd: Mapped[Decimal] = mapped_column(Numeric(28, 2), nullable=False)
    suggested_whole_shares: Mapped[Decimal] = mapped_column(Numeric(28, 0), nullable=False)
    score: Mapped[Decimal] = mapped_column(Numeric(7, 2), nullable=False)
    reasons: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    persisted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class LongPortfolioStateRecord(Base):
    """Minimal mutable confirmation state for restart-safe LONG portfolio processing."""

    __tablename__ = "long_portfolio_states"
    __table_args__ = (
        CheckConstraint("jsonb_typeof(qualified_sessions) = 'array'", name="sessions_array"),
        Index("long_portfolio_states_updated_idx", "updated_at"),
        {"schema": SCHEMA},
    )

    rule_version: Mapped[str] = mapped_column(Text, primary_key=True)
    symbol: Mapped[str] = mapped_column(Text, primary_key=True)
    qualified_sessions: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    last_emitted: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MarketBarRecord(Base):
    """Recoverable normalized bar cache written only by MarketData History."""

    __tablename__ = "market_bars"
    __table_args__ = (
        CheckConstraint(
            "timeframe in ('1Min', '15Min', '1Hour', '1Day', '1Week')",
            name="timeframe",
        ),
        CheckConstraint("volume >= 0", name="volume_nonnegative"),
        CheckConstraint(
            "high >= open and high >= low and high >= close "
            "and low <= open and low <= high and low <= close",
            name="ohlc",
        ),
        Index("market_bars_history_idx", "symbol", "timeframe", "timestamp"),
        {"schema": SCHEMA},
    )

    symbol: Mapped[str] = mapped_column(Text, primary_key=True)
    timeframe: Mapped[str] = mapped_column(Text, primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    open: Mapped[Decimal] = mapped_column(Numeric(28, 8), nullable=False)
    high: Mapped[Decimal] = mapped_column(Numeric(28, 8), nullable=False)
    low: Mapped[Decimal] = mapped_column(Numeric(28, 8), nullable=False)
    close: Mapped[Decimal] = mapped_column(Numeric(28, 8), nullable=False)
    volume: Mapped[Decimal] = mapped_column(Numeric(28, 8), nullable=False)
    trade_count: Mapped[int | None] = mapped_column(BigInteger)
    vwap: Mapped[Decimal | None] = mapped_column(Numeric(28, 8))
    source: Mapped[str] = mapped_column(Text, nullable=False)
    feed: Mapped[str] = mapped_column(Text, nullable=False)
    is_final: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    downloaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class PatreonCapsWatchRecord(Base):
    __tablename__ = "patreon_caps_watches"
    __table_args__ = (
        CheckConstraint(
            "state in ('WATCH_ZONE', 'SUPPORT_TEST', 'CONFIRMED_V', 'CONFIRMED_BASE', "
            "'IMPULSE_RETEST', 'INVALIDATED', 'EXPIRED')",
            name="state",
        ),
        CheckConstraint(
            "invalidation < zone_low and zone_low <= zone_center and zone_center <= zone_high",
            name="levels",
        ),
        Index(
            "patreon_caps_one_active_per_symbol_version_idx",
            "symbol",
            "rule_version",
            unique=True,
            postgresql_where=text("state NOT IN ('INVALIDATED', 'EXPIRED')"),
        ),
        Index("patreon_caps_watches_state_expires_idx", "state", "expires_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    rule_version: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    armed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    zone_low: Mapped[Decimal] = mapped_column(Numeric(28, 8), nullable=False)
    zone_center: Mapped[Decimal] = mapped_column(Numeric(28, 8), nullable=False)
    zone_high: Mapped[Decimal] = mapped_column(Numeric(28, 8), nullable=False)
    invalidation: Mapped[Decimal] = mapped_column(Numeric(28, 8), nullable=False)
    highest_price: Mapped[Decimal] = mapped_column(Numeric(28, 8), nullable=False)
    tranche_stage: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    saw_macro_shock: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    support_sources: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    source_analysis_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class PatreonCapsTransitionRecord(Base):
    __tablename__ = "patreon_caps_transitions"
    __table_args__ = (
        UniqueConstraint("deduplication_key", name="patreon_caps_transitions_dedup_key"),
        CheckConstraint("patreon_score >= 0 and patreon_score <= 100", name="score"),
        Index("patreon_caps_transitions_symbol_occurred_idx", "symbol", "occurred_at"),
        Index("patreon_caps_transitions_watch_occurred_idx", "watch_id", "occurred_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    deduplication_key: Mapped[str] = mapped_column(Text, nullable=False)
    watch_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.patreon_caps_watches.id", ondelete="RESTRICT"),
        nullable=False,
    )
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    previous_state: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    rule_version: Mapped[str] = mapped_column(Text, nullable=False)
    current_price: Mapped[Decimal] = mapped_column(Numeric(28, 8), nullable=False)
    patreon_score: Mapped[Decimal] = mapped_column(Numeric(7, 2), nullable=False)
    tranche_stage: Mapped[int | None] = mapped_column(Integer)
    suggested_tranche_usd: Mapped[Decimal | None] = mapped_column(Numeric(28, 2))
    suggested_whole_shares: Mapped[Decimal | None] = mapped_column(Numeric(28, 0))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    persisted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
