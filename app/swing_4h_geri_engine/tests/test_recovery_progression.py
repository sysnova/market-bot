from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.contracts import BarTimeframe
from app.swing_4h_geri_engine.models import Swing4HGeriContext
from app.swing_4h_geri_engine.recovery import RecoveryRules
from app.swing_4h_geri_engine.recovery_progression import progressive_recovery_metrics
from app.swing_4h_geri_engine.tests.test_recovery import context
from app.swing_4h_geri_engine.tests.test_v18 import _bar


def progression_context(count: int = 3, *, next_target: bool = True) -> Swing4HGeriContext:
    c = context()
    extra = tuple(
        _bar(i, *v).model_copy(
            update={
                "timeframe": BarTimeframe.MINUTE_15,
                "timestamp": c.confirmation_bars[-1].timestamp + timedelta(minutes=15 * (i + 1)),
            }
        )
        for i, v in enumerate(
            (("100", "110.2", "101"), ("109", "112", "111.5"), ("110.8", "112.5", "112"))
        )
    )[:count]
    daily = tuple(
        _bar(i, "104", high, "108").model_copy(
            update={
                "timeframe": BarTimeframe.DAY_1,
                "timestamp": datetime(2026, 7, 15 + i, 4, tzinfo=UTC),
            }
        )
        for i, high in enumerate(("119", "120", "119"))
    )
    fast = (*c.confirmation_bars, *extra)
    at = fast[-1].timestamp + timedelta(minutes=15)
    return replace(
        c,
        confirmation_bars=fast,
        daily_bars=daily if next_target else (),
        current_price=fast[-1].close,
        current_price_at=at,
        as_of=at,
    )


def values(c: Swing4HGeriContext) -> dict[str, object]:
    return {m.name: m.value for m in progressive_recovery_metrics(c, RecoveryRules())}


def test_resistance_touch_preserves_recovery_but_does_not_buy() -> None:
    m = values(progression_context(1))
    assert m["countertrend_maturity"] == "CT2"
    assert m["countertrend_eligible"] is False
    assert "resistance_acceptance_pending" in m["countertrend_eligibility_reasons"]


def test_breakout_needs_subsequent_acceptance_before_renewing_levels() -> None:
    m = values(progression_context(2))
    assert m["countertrend_target"] == Decimal("110")
    assert m["countertrend_eligible"] is False


def test_accepted_resistance_creates_new_entry_with_higher_stop_and_next_target() -> None:
    before = values(progression_context(0))
    after = values(progression_context())
    assert after["countertrend_maturity"] == "CT2"
    assert after["countertrend_entry_sequence"] == 1
    assert after["countertrend_target"] == Decimal("120")
    assert before["countertrend_invalidation"] < after["countertrend_invalidation"] < Decimal("112")
    assert after["countertrend_eligible"] is True
    assert after["countertrend_entry_accepted_at"] != before["countertrend_entry_accepted_at"]
    assert after["countertrend_accepted_at"] == before["countertrend_accepted_at"]


def test_next_target_cannot_be_invented() -> None:
    m = values(progression_context(next_target=False))
    assert m["countertrend_maturity"] == "CT2"
    assert m["countertrend_target"] is None
    assert m["countertrend_eligible"] is False
    assert "NO_VALID_DIRECTIONAL_TARGET" in m["countertrend_eligibility_reasons"]


def test_failed_acceptance_does_not_reset_stop_or_advance_target() -> None:
    c = progression_context()
    failed = c.confirmation_bars[-1].model_copy(update={"low": Decimal("108")})
    m = values(replace(c, confirmation_bars=(*c.confirmation_bars[:-1], failed)))
    assert m["countertrend_target"] == Decimal("110")
    assert m["countertrend_eligible"] is False


def test_new_entry_stop_failure_does_not_erase_structural_maturity() -> None:
    c = progression_context()
    failure = c.confirmation_bars[-1].model_copy(
        update={
            "timestamp": c.confirmation_bars[-1].timestamp + timedelta(minutes=15),
            "low": Decimal("107"),
            "close": Decimal("108"),
            "open": Decimal("109"),
        }
    )
    at = failure.timestamp + timedelta(minutes=15)
    m = values(
        replace(
            c,
            confirmation_bars=(*c.confirmation_bars, failure),
            current_price=failure.close,
            current_price_at=at,
            as_of=at,
        )
    )
    assert m["countertrend_maturity"] == "CT2"
    assert m["countertrend_eligible"] is False
    assert "recovery_entry_invalidated" in m["countertrend_eligibility_reasons"]
