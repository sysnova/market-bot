"""Strict JSON wire encoding for the public event envelope."""

from __future__ import annotations

from app.common.canonical import canonical_json
from app.contracts import EventEnvelope


def encode_envelope(envelope: EventEnvelope) -> bytes:
    """Encode a stable snapshot instead of retaining mutable nested values."""

    return canonical_json(envelope)


def decode_envelope(payload: bytes) -> EventEnvelope:
    return EventEnvelope.model_validate_json(payload, strict=True)
