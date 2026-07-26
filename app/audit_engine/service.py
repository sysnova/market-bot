"""Audit application service composed through the shared event-bus port."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from uuid import UUID

from app.contracts import EventBus, EventEnvelope, Subscription, SubscriptionOptions

from .errors import InvalidAuditEventError
from .store import AuditLog, AuditStream


@dataclass(frozen=True, slots=True)
class AuditConfirmation:
    """Service-level acknowledgement returned after durable processing."""

    event_id: UUID
    run_id: str
    stream: AuditStream
    persisted: bool
    duplicate: bool


class AuditService:
    """Translate routed envelopes into durable audit records."""

    DEFAULT_SUBJECT = "audit.>"
    DURABLE_NAME = "audit-engine-v1"

    def __init__(self, runtime_root: Path | str) -> None:
        self._log = AuditLog(runtime_root)

    def process(self, envelope: EventEnvelope) -> AuditConfirmation:
        run_id, stream = _routing_metadata(envelope)
        receipt = self._log.append(run_id, stream, envelope)
        return AuditConfirmation(
            event_id=envelope.event_id,
            run_id=run_id,
            stream=stream,
            persisted=receipt.persisted,
            duplicate=receipt.duplicate,
        )

    async def start(
        self,
        bus: EventBus,
        subject: str = DEFAULT_SUBJECT,
    ) -> Subscription:
        """Subscribe a handler whose successful return acknowledges persistence."""

        async def handle(envelope: EventEnvelope) -> None:
            self.process(envelope)

        options = SubscriptionOptions(
            durable_name=self.DURABLE_NAME,
            replay_all=True,
        )
        return await bus.subscribe(subject, handle, options=options)

    def close(self) -> None:
        self._log.close()


def _routing_metadata(envelope: EventEnvelope) -> tuple[str, AuditStream]:
    payload = envelope.payload
    if not isinstance(payload, Mapping):
        raise InvalidAuditEventError("audit envelope payload must be an object")
    payload_map = cast("Mapping[object, object]", payload)
    audit = payload_map.get("audit")
    if not isinstance(audit, Mapping):
        raise InvalidAuditEventError("audit envelope payload requires an audit object")
    audit_map = cast("Mapping[object, object]", audit)
    run_id = audit_map.get("run_id")
    stream_value = audit_map.get("stream")
    if not isinstance(run_id, str):
        raise InvalidAuditEventError("audit.run_id must be a string")
    if not isinstance(stream_value, str):
        raise InvalidAuditEventError("audit.stream must be a string")
    try:
        stream = AuditStream(stream_value)
    except ValueError as error:
        raise InvalidAuditEventError(f"unknown audit stream: {stream_value}") from error
    return run_id, stream
