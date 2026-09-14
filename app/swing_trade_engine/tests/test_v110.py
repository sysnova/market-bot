from dataclasses import replace
from decimal import Decimal
from unittest.mock import patch

from app.contracts import NamedValue, SwingTradeMaturity
from app.swing_trade_engine.tests.test_engine import analyze
from app.swing_trade_engine.tests.test_v17 import append_bar, context_at, values
from app.swing_trade_engine.v18 import SwingTradeEngineV18
from app.swing_trade_engine.v110 import SwingTradeEngineV110

Candidate = SwingTradeEngineV110


def test_se_reference_keeps_fibonacci_but_exports_only_valid_entry_zone() -> None:
    native = analyze("97").model_copy(
        update={
            "symbol": "SE",
            "maturity": SwingTradeMaturity.ST2,
            "eligible": True,
            "current_price": Decimal("109.69"),
            "zone_low": Decimal("103.3451"),
            "zone_high": Decimal("108.8050"),
            "invalidation": Decimal("104.1820"),
            "support_band_low": Decimal("105.1810"),
        }
    )
    with patch.object(SwingTradeEngineV18, "analyze", return_value=native):
        result = Candidate().analyze(context_at(6))
    assert result.zone_low == Decimal("103.3451")
    assert result.invalidation == Decimal("104.1820")
    assert result.entry_zone_low == Decimal("105.1810")
    assert result.entry_zone_high == Decimal("108.8050")
    assert result.entry_invalidation == Decimal("104.1820")
    assert result.maturity is SwingTradeMaturity.ST2


def test_no_valid_zone_does_not_move_stop_or_emit_reference() -> None:
    native = analyze("97").model_copy(
        update={
            "maturity": SwingTradeMaturity.ST2,
            "eligible": True,
            "zone_low": Decimal("90"),
            "zone_high": Decimal("100"),
            "invalidation": Decimal("101"),
            "support_band_low": Decimal("102"),
        }
    )
    with patch.object(SwingTradeEngineV18, "analyze", return_value=native):
        result = Candidate().analyze(context_at(6))
    assert result.invalidation == Decimal("101")
    assert result.maturity is None
    assert not result.eligible
    assert result.entry_zone_low is None
    assert "no_valid_entry_zone_above_invalidation" in result.reasons


def test_confirmed_rebound_uses_frozen_entry_above_its_operational_stop() -> None:
    native = analyze("97").model_copy(
        update={
            "maturity": SwingTradeMaturity.ST3,
            "metrics": (
                NamedValue(name="rebound_state", value="OPEN"),
                NamedValue(name="rebound_entry_price", value="109.69"),
                NamedValue(name="operational_stop", value="108.3025"),
            ),
        }
    )
    with patch.object(SwingTradeEngineV18, "analyze", return_value=native):
        result = Candidate().analyze(context_at(6))
    assert result.entry_zone_low == result.entry_zone_high == Decimal("109.69")
    assert result.entry_invalidation == Decimal("108.3025")


def test_upgrade_preserves_original_entry_zone_and_stop_after_stop_exit() -> None:
    ctx = context_at(7)
    last = ctx.confirmation_bars[-1].model_copy(update={"close": Decimal("101.5")})
    ctx = replace(
        ctx, confirmation_bars=(*ctx.confirmation_bars[:-1], last), current_price=last.close
    )
    previous = SwingTradeEngineV18().analyze(ctx)
    assert previous.maturity is SwingTradeMaturity.ST3
    exited = Candidate().analyze(
        replace(append_bar(ctx, "96", high="97", low="95"), previous_assessment=previous)
    )
    assert "rebound_stop_breached" in exited.reasons
    assert exited.entry_invalidation == values(previous)["operational_stop"]
    assert exited.entry_zone_low == exited.entry_zone_high == Decimal("101.5")
