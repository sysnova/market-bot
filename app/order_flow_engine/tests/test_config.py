from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from app.order_flow_engine.config import load_order_flow_policy

ROOT = Path(__file__).resolve().parents[3]


def test_v12_policy_loads_actionability_and_stability_thresholds() -> None:
    policy = load_order_flow_policy(ROOT / "configs/rules/order_flow/1.2.0.yaml")

    assert policy.tracked_symbols == ("ASTS", "ASTX", "ASTN", "NBIS", "NBIZ")
    assert policy.quote_max_age == timedelta(seconds=2)
    assert policy.minimum_trades == 5
    assert policy.minimum_volume == Decimal("250")
    assert policy.pressure_ratio == Decimal("0.68")
    assert policy.transition_confirmation_samples == 3
    assert policy.transition_confirmation_seconds == Decimal("3")
    assert policy.reversal_confirmation_samples == 5
    assert policy.reversal_confirmation_seconds == Decimal("5")
    assert policy.neutral_confirmation_samples == 4
    assert policy.neutral_confirmation_seconds == Decimal("4")
