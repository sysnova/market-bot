"""Materialized current events; history remains owned by JetStream."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol, cast

from app.contracts import EventEnvelope


class CurrentEventStore(Protocol):
    async def get(self, subject: str) -> EventEnvelope | None: ...
    async def put(self, subject: str, envelope: EventEnvelope) -> None: ...


_factory: Callable[[str, str], CurrentEventStore] | None = None


def configure_current_events(factory: Callable[[str, str], CurrentEventStore]) -> None:
    global _factory
    _factory = factory


def current_events(prefix: str, stream: str) -> CurrentEventStore | None:
    return _factory(prefix, stream) if _factory is not None else None


def event_order(envelope: EventEnvelope) -> str:
    """Use evaluation time for analyses, matching the dashboard evidence book."""
    payload = envelope.model_dump(mode="json").get("payload")
    if envelope.event_type == "analysis.result.produced" and isinstance(payload, dict):
        for field in (
            "assessed_at",
            "generated_at",
            "updated_at",
            "data_as_of",
            "as_of",
            "occurred_at",
            "created_at",
        ):
            value = cast(dict[str, Any], payload).get(field)
            if isinstance(value, str):
                try:
                    at = datetime.fromisoformat(value)
                except ValueError:
                    continue
                if at.tzinfo is not None:
                    return at.astimezone(UTC).isoformat(timespec="microseconds")
    return envelope.occurred_at.astimezone(UTC).isoformat(timespec="microseconds")
