"""SQLAlchemy mappings for the private ``market_bot`` PostgreSQL schema."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
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

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_entity_id
    )
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

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_entity_id
    )
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

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_entity_id
    )
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
        UniqueConstraint(
            "run_id", "strategy_version_id", name="run_strategies_assignment_key"
        ),
        CheckConstraint(
            "mode in ('PRIMARY', 'SHADOW', 'RESEARCH', 'DISABLED')", name="mode"
        ),
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

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_entity_id
    )
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

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_entity_id
    )
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

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_entity_id
    )
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
        UniqueConstraint(
            "consumer_name", "stream", name="consumer_checkpoints_position_key"
        ),
        CheckConstraint("sequence >= 0", name="sequence_nonnegative"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_entity_id
    )
    consumer_name: Mapped[str] = mapped_column(Text, nullable=False)
    stream: Mapped[str] = mapped_column(Text, nullable=False)
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class ServiceHealthRecord(Base):
    __tablename__ = "service_health"
    __table_args__ = (
        UniqueConstraint("service_name", name="service_health_service_name_key"),
        CheckConstraint(
            "status in ('HEALTHY', 'DEGRADED', 'UNHEALTHY', 'UNKNOWN')", name="status"
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_entity_id
    )
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

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_entity_id
    )
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    run_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey(f"{SCHEMA}.runs.id", ondelete="RESTRICT")
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
