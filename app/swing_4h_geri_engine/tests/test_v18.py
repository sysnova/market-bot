from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.contracts import BarTimeframe, GeriLevelKind, GeriMaturity, MarketBar
from app.swing_4h_geri_engine.models import Swing4HGeriContext
from app.swing_4h_geri_engine.v16 import Swing4HGeriEngineV16
from app.swing_4h_geri_engine.v18 import Swing4HGeriEngineV18

START = datetime(2026, 7, 20, 13, 30, tzinfo=UTC)


def _bar(index: int, low: str, high: str, close: str) -> MarketBar:
    return MarketBar(
        symbol="HUT",
        timeframe=BarTimeframe.HOUR_4,
        timestamp=START + timedelta(hours=4 * index),
        open=Decimal(close) - Decimal("1"),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal("1000"),
        source="test",
        feed="sip",
        is_final=True,
    )


def _stale_short_then_recent_long() -> tuple[MarketBar, ...]:
    values = (
        ("105", "108", "107"),
        ("107", "110", "108"),
        ("101", "106", "102"),
        ("94", "103", "95"),
        ("96", "104", "103"),
        ("108", "112", "111"),
        ("106", "110", "107"),
        ("99", "107", "100"),
        ("92", "101", "93"),
        ("79", "83", "81"),
        ("77", "82", "79"),
        ("80", "86", "85"),
        ("83", "90", "89"),
        ("80", "88", "81"),
        ("75", "82", "76"),
        ("73", "81", "75"),
        ("74", "85", "83"),
        ("81", "92", "91"),
    )
    return tuple(_bar(index, *value) for index, value in enumerate(values))


def _context(bars: tuple[MarketBar, ...], price: str = "80") -> Swing4HGeriContext:
    observed_at = bars[-1].timestamp + timedelta(hours=4)
    return Swing4HGeriContext(
        symbol="HUT",
        bars=bars,
        current_price=Decimal(price),
        as_of=observed_at,
        current_price_at=observed_at,
    )


def test_v18_rebases_a_completed_chain_that_is_structurally_detached_from_price() -> None:
    bars = _stale_short_then_recent_long()
    context = _context(bars)

    stale = Swing4HGeriEngineV16().analyze(context)
    rebased = Swing4HGeriEngineV18().analyze(context)

    assert stale.maturity is GeriMaturity.EXTENDED
    assert stale.active_level_price == Decimal("112")
    assert [(level.kind, level.price) for level in rebased.levels] == [
        (GeriLevelKind.SUPPORT, Decimal("77")),
        (GeriLevelKind.RESISTANCE, Decimal("90")),
        (GeriLevelKind.SUPPORT, Decimal("73")),
    ]
    assert rebased.maturity is GeriMaturity.ARMED
    assert rebased.active_level_price == Decimal("73")
    assert "structural_chain_rebased" in rebased.reasons
    metrics = {item.name: item.value for item in rebased.metrics}
    assert metrics["rebase_previous_active_level"] == Decimal("112")


def test_v18_keeps_an_extended_chain_until_it_crosses_the_rebase_threshold() -> None:
    bars = _stale_short_then_recent_long()[:9]

    result = Swing4HGeriEngineV18(structural_rebase_atr=Decimal("10")).analyze(_context(bars))

    assert result.maturity is GeriMaturity.EXTENDED
    assert result.active_level_price == Decimal("112")
    assert "structural_chain_rebased" not in result.reasons
