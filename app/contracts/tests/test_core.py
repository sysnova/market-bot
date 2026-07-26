from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.contracts import (
    AlertPolicy,
    EventEnvelope,
    RuleLifecycleStatus,
    RuleMetadata,
    RuleStatus,
    RuleType,
    StrategyMode,
)

NOW = datetime(2026, 7, 25, 18, 0, tzinfo=UTC)
HASH = "sha256:" + "a" * 64


def test_public_enums_are_stable() -> None:
    assert StrategyMode.PRIMARY.value == "PRIMARY"
    assert set(RuleStatus) == {
        RuleStatus.PASS,
        RuleStatus.FAIL,
        RuleStatus.NOT_APPLICABLE,
        RuleStatus.ERROR,
    }


def test_models_are_strict_frozen_and_forbid_extra_fields() -> None:
    policy = AlertPolicy(policy_id="desk", min_confidence=Decimal("0.75"))
    with pytest.raises(ValidationError):
        AlertPolicy(policy_id="desk", min_confidence="0.75")
    with pytest.raises(ValidationError):
        AlertPolicy(policy_id="desk", min_confidence=Decimal("0.75"), surprise=True)
    with pytest.raises(ValidationError):
        policy.min_confidence = Decimal("0.8")


def test_event_envelope_generates_uuid7_and_requires_utc() -> None:
    event = EventEnvelope(event_type="pattern.detected", source="scanner")
    assert event.event_id.version == 7
    assert event.occurred_at.utcoffset() == timedelta(0)
    assert event.schema_version == "1.0.0"
    with pytest.raises(ValidationError):
        EventEnvelope(
            event_type="pattern.detected",
            source="scanner",
            occurred_at=datetime(2026, 1, 1),
        )


def test_typed_hash_rejects_missing_prefix() -> None:
    with pytest.raises(ValidationError):
        RuleMetadata(
            rule_id="trend.confirm",
            name="Trend confirmation",
            version="1.0.0",
            rule_type=RuleType.CONFIRMATION,
            lifecycle_status=RuleLifecycleStatus.DRAFT,
            description="Confirms a trend.",
            implementation_hash="a" * 64,
            created_at=NOW,
        )
