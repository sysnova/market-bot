"""Public models for the standalone JetStream connector."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .catalog import FilterPlan
from .contracts import EventEnvelope


@dataclass(frozen=True, slots=True)
class ConnectorConfig:
    """Connection, delivery-position, and backpressure settings."""

    filters: FilterPlan
    url: str = "nats://10.77.77.1:4222"
    stream: str = "MARKETBOT"
    start_at: datetime | None = None
    durable_name: str | None = None
    batch_size: int = 100
    max_ack_pending: int = 1_000
    fetch_timeout_seconds: float = 1.0
    ack_wait_seconds: float = 30.0
    max_deliver: int = 5
    redelivery_delay_seconds: float = 0.1

    def __post_init__(self) -> None:
        if not self.url.startswith(("nats://", "tls://")):
            raise ValueError("url must use nats:// or tls://")
        if not self.stream.strip():
            raise ValueError("stream cannot be blank")
        if self.start_at is not None:
            if self.start_at.tzinfo is None or self.start_at.utcoffset() is None:
                raise ValueError("start_at must include a timezone offset")
            object.__setattr__(self, "start_at", self.start_at.astimezone(UTC))
        if self.durable_name is not None:
            validate_durable_name(self.durable_name)
        if self.batch_size < 1:
            raise ValueError("batch_size must be positive")
        if self.max_ack_pending < self.batch_size:
            raise ValueError("max_ack_pending must be at least batch_size")
        if self.fetch_timeout_seconds <= 0:
            raise ValueError("fetch_timeout_seconds must be positive")
        if self.ack_wait_seconds <= 0:
            raise ValueError("ack_wait_seconds must be positive")
        if self.max_deliver < 1:
            raise ValueError("max_deliver must be positive")
        if self.redelivery_delay_seconds < 0:
            raise ValueError("redelivery_delay_seconds cannot be negative")


@dataclass(frozen=True, slots=True)
class ConnectorMessage:
    """Decoded delivery plus transport metadata and poison-message evidence."""

    nats_subject: str
    stream_sequence: int
    consumer_sequence: int
    delivered_count: int
    stored_at: datetime
    envelope: EventEnvelope | None
    raw_data: bytes | None = None
    decode_error: str | None = None

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "nats_subject": self.nats_subject,
            "stream_sequence": self.stream_sequence,
            "consumer_sequence": self.consumer_sequence,
            "delivered_count": self.delivered_count,
            "redelivered": self.delivered_count > 1,
            "stored_at": self.stored_at.isoformat(),
            "envelope": (
                self.envelope.model_dump(mode="json") if self.envelope is not None else None
            ),
            "raw_base64": (
                base64.b64encode(self.raw_data).decode("ascii")
                if self.raw_data is not None
                else None
            ),
            "decode_error": self.decode_error,
        }


def parse_start_at(value: str) -> datetime:
    """Parse RFC 3339 input while requiring an explicit timezone."""

    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise ValueError("start_at must be an RFC 3339 datetime") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("start_at must include a timezone offset")
    return parsed.astimezone(UTC)


def validate_durable_name(value: str) -> None:
    if not value or value != value.strip():
        raise ValueError("durable_name cannot be blank or padded")
    forbidden = set(".*>/\\ \t\r\n")
    if any(character in forbidden for character in value):
        raise ValueError("durable_name contains a forbidden NATS character")
