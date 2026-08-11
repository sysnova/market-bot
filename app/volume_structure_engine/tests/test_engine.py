from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.contracts import (
    AnalysisHorizon,
    AnalysisVerdict,
    BarTimeframe,
    MarketBar,
    PatternDirection,
)
from app.volume_structure_engine import (
    VolumeStructureContext,
    VolumeStructureEngine,
    VolumeStructureEngineV11,
)


def test_weekly_lower_price_low_and_higher_obv_low_confirms_reclaimed_divergence() -> None:
    result = VolumeStructureEngine().evaluate(
        VolumeStructureContext(symbol="VLO", weekly_bars=_bullish_divergence_bars())
    )

    metrics = {item.name: item.value for item in result.metrics}
    assert result.horizon is AnalysisHorizon.VOLUME_STRUCTURE
    assert result.verdict is AnalysisVerdict.FAVORABLE
    assert result.direction is PatternDirection.BULLISH
    assert metrics["divergence_state"] == "RECLAIM_CONFIRMED"
    assert metrics["price_pivot_2"] < metrics["price_pivot_1"]
    assert metrics["obv_pivot_2"] > metrics["obv_pivot_1"]
    assert metrics["evidence_boost"] == Decimal("10")
    assert "weekly_obv_bullish_divergence" in result.reasons


def test_price_and_obv_lower_lows_do_not_create_bullish_divergence() -> None:
    bars = list(_bullish_divergence_bars())
    for index in range(7, 10):
        bars[index] = bars[index].model_copy(update={"volume": Decimal("5000")})

    result = VolumeStructureEngine().evaluate(
        VolumeStructureContext(symbol="VLO", weekly_bars=tuple(bars))
    )

    metrics = {item.name: item.value for item in result.metrics}
    assert result.verdict is AnalysisVerdict.WATCH
    assert result.direction is PatternDirection.NEUTRAL
    assert metrics["divergence_state"] == "NO_DIVERGENCE"
    assert metrics["evidence_boost"] == Decimal("0")


def test_later_weekly_close_below_invalidation_revokes_divergence_boost() -> None:
    bars = list(_bullish_divergence_bars())
    bars[13] = bars[13].model_copy(
        update={"low": Decimal("80"), "close": Decimal("81")}
    )

    result = VolumeStructureEngineV11().evaluate(
        VolumeStructureContext(symbol="VLO", weekly_bars=tuple(bars))
    )

    metrics = {item.name: item.value for item in result.metrics}
    assert result.engine_version == "1.1.0"
    assert result.verdict is AnalysisVerdict.WATCH
    assert result.direction is PatternDirection.NEUTRAL
    assert metrics["divergence_state"] == "DIVERGENCE_INVALIDATED"
    assert metrics["evidence_boost"] == Decimal("0")
    assert metrics["invalidation_breached_at"] == bars[13].timestamp
    assert metrics["invalidation_breach_close"] == Decimal("81")
    assert "weekly_obv_divergence_invalidation_breached" in result.reasons


def _bullish_divergence_bars() -> tuple[MarketBar, ...]:
    lows = (100, 98, 94, 90, 94, 97, 101, 99, 95, 85, 96, 100, 103, 104, 105)
    closes = (105, 102, 98, 92, 95, 99, 103, 101, 96, 88, 98, 102, 106, 108, 110)
    volumes = (500, 1000, 1000, 1000, 500, 500, 500, 100, 100, 100, 500, 500, 500, 500, 500)
    start = datetime(2026, 4, 24, 20, tzinfo=UTC)
    return tuple(
        MarketBar(
            symbol="VLO",
            timeframe=BarTimeframe.WEEK_1,
            timestamp=start + timedelta(weeks=index),
            open=Decimal(str(close + 1)),
            high=Decimal(str(max(close + 2, low + 3))),
            low=Decimal(str(low)),
            close=Decimal(str(close)),
            volume=Decimal(str(volume)),
            source="fixture",
            feed="test",
        )
        for index, (low, close, volume) in enumerate(zip(lows, closes, volumes, strict=True))
    )
