from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from app.contracts import (
    GammaAssessment,
    GammaExpirationAssessment,
    options_gamma_assessment_subject,
)

NOW = datetime(2026, 8, 12, 15, tzinfo=UTC)
HASH = f"sha256:{'a' * 64}"


def expiration() -> GammaExpirationAssessment:
    return GammaExpirationAssessment(
        expiration_date=date(2026, 8, 14),
        days_to_expiration=2,
        contract_count=6,
        usable_contract_count=6,
        open_interest=Decimal("3300"),
        net_gamma_exposure=Decimal("120000"),
        absolute_gamma_exposure=Decimal("950000"),
        call_wall=Decimal("105"),
        put_wall=Decimal("95"),
        absolute_gamma_wall=Decimal("100"),
        max_pain=Decimal("100"),
        gamma_flip=Decimal("98.50"),
        expected_move_low=Decimal("93"),
        expected_move_high=Decimal("107"),
        influence_weight=Decimal("1"),
    )


def assessment(**updates: object) -> GammaAssessment:
    values: dict[str, object] = {
        "symbol": "AAPL",
        "generated_at": NOW,
        "expires_at": NOW + timedelta(minutes=20),
        "engine_version": "1.0.0",
        "methodology_version": "1.0.0",
        "spot_price": Decimal("100"),
        "spot_as_of": NOW,
        "expiration_from": date(2026, 8, 12),
        "expiration_to": date(2026, 9, 25),
        "open_interest_as_of": date(2026, 8, 11),
        "status": "AVAILABLE",
        "quality_score": Decimal("96"),
        "contract_count": 6,
        "usable_contract_count": 6,
        "coverage_ratio": Decimal("1"),
        "gamma_regime": "MIXED",
        "directional_bias": "NEUTRAL",
        "net_gamma_exposure": Decimal("120000"),
        "absolute_gamma_exposure": Decimal("950000"),
        "net_gamma_ratio": Decimal("0.1263"),
        "call_wall": Decimal("105"),
        "put_wall": Decimal("95"),
        "absolute_gamma_wall": Decimal("100"),
        "max_pain": Decimal("100"),
        "gamma_flip": Decimal("98.50"),
        "expected_move_low": Decimal("93"),
        "expected_move_high": Decimal("107"),
        "pin_risk": True,
        "acceleration_risk": False,
        "dealer_sign_assumption": "CALL_POSITIVE_PUT_NEGATIVE",
        "expirations": (expiration(),),
        "warnings": (),
        "context_hash": HASH,
    }
    values.update(updates)
    return GammaAssessment(**values)  # type: ignore[arg-type]


def test_options_gamma_subject_is_stable_and_symbol_safe() -> None:
    assert options_gamma_assessment_subject("BRK.B") == (
        "marketbot.v1.options-gamma.assessment.BRK_B"
    )


def test_gamma_assessment_requires_future_expiry_and_consistent_coverage() -> None:
    item = assessment()

    assert item.assessment_id.version == 7
    assert item.expirations[0].max_pain == Decimal("100")

    with pytest.raises(ValueError, match="expires_at"):
        assessment(expires_at=NOW)
    with pytest.raises(ValueError, match="coverage_ratio"):
        assessment(coverage_ratio=Decimal("0.5"))


def test_unavailable_gamma_cannot_publish_trade_levels() -> None:
    with pytest.raises(ValueError, match="UNAVAILABLE"):
        assessment(
            status="UNAVAILABLE",
            quality_score=Decimal("0"),
            usable_contract_count=0,
            coverage_ratio=Decimal("0"),
        )
