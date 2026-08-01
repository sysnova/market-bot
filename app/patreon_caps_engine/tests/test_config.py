from decimal import Decimal
from pathlib import Path

from app.contracts import MacroRegime
from app.patreon_caps_engine import load_patreon_caps_policy


def test_versioned_policy_artifact_loads_exact_thresholds() -> None:
    path = Path("configs/rules/patreon_caps/1.0.0.yaml")

    policy = load_patreon_caps_policy(path)

    assert policy.rule_version == "1.0.0"
    assert policy.portfolio_capital_usd == Decimal("103000")
    assert policy.macro_thresholds[MacroRegime.RISK_OFF] == Decimal("85")
    assert policy.macro_symbols == ("UUP", "VIXY", "TLT", "IEF", "SPY")


def test_lesson_consolidation_is_an_explicit_new_artifact() -> None:
    policy = load_patreon_caps_policy(Path("configs/rules/patreon_caps/1.1.0.yaml"))

    assert policy.rule_version == "1.1.0"
    assert policy.lesson_enabled is True
    assert policy.require_daily_above_sma200 is True
    assert policy.lesson_weight == Decimal("0.20")
    assert policy.confluence_weight == Decimal("0.35")
