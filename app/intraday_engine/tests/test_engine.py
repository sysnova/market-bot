import json
from decimal import Decimal
from pathlib import Path

from app.contracts import (
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    BarTimeframe,
    PatternDirection,
)
from app.intraday_engine.engine import IntradayEngine
from app.intraday_engine.models import IntradayContext
from app.intraday_engine.v2 import IntradayEngineV2

from .helpers import trend_bars

FIXTURES = Path(__file__).parents[1] / "fixtures" / "cases.json"


def _metric(result: AnalysisResult, name: str) -> object:
    return next(metric.value for metric in result.metrics if metric.name == name)


def test_fixture_breakout_and_breakdown_are_directional_and_auditable() -> None:
    cases = json.loads(FIXTURES.read_text(encoding="utf-8"))
    engine = IntradayEngine()

    for case in cases:
        bars = trend_bars(
            symbol=case["symbol"],
            start=Decimal(case["start"]),
            step=Decimal(case["step"]),
            final_move=Decimal(case["final_move"]),
            base_volume=Decimal(case["base_volume"]),
            final_volume=Decimal(case["final_volume"]),
        )
        context = IntradayContext(
            symbol=case["symbol"],
            as_of=bars[-1].timestamp,
            minute_bars=bars,
        )

        first = engine.analyze(context)
        second = engine.analyze(context)

        assert first.horizon is AnalysisHorizon.INTRADAY
        assert first.verdict is AnalysisVerdict.FAVORABLE
        assert first.direction is PatternDirection(case["expected_direction"])
        assert _metric(first, "setup") == case["expected_setup"]
        assert Decimal(str(_metric(first, "relative_volume"))) >= Decimal("2")
        assert Decimal(str(_metric(first, "reward_risk_ratio"))) >= Decimal("1.5")
        assert first.context_hash == second.context_hash
        assert first.analysis_id == second.analysis_id
        assert first.reasons


def test_bullish_vwap_reclaim_is_detected_without_order_semantics() -> None:
    bars = list(
        trend_bars(
            symbol="AAPL",
            start=Decimal("101"),
            step=Decimal("-0.025"),
            final_move=Decimal("0.80"),
            base_volume=Decimal("1000"),
            final_volume=Decimal("2200"),
            count=40,
        )
    )
    context = IntradayContext(
        symbol="AAPL",
        as_of=bars[-1].timestamp,
        minute_bars=tuple(bars),
    )

    result = IntradayEngine().analyze(context)

    assert result.direction is PatternDirection.BULLISH
    assert _metric(result, "setup") in {"bullish_breakout", "bullish_vwap_reclaim"}
    metric_names = {metric.name for metric in result.metrics}
    assert {"reference_price", "invalidation_level", "objective_level"} <= metric_names
    assert "quantity" not in metric_names
    assert "order" not in " ".join(result.reasons).lower()


def test_insufficient_history_is_explicit_and_has_no_levels() -> None:
    bars = trend_bars(
        symbol="NVDA",
        start=Decimal("150"),
        step=Decimal("0.02"),
        final_move=Decimal("0.02"),
        base_volume=Decimal("1000"),
        final_volume=Decimal("1000"),
        count=20,
    )
    context = IntradayContext(
        symbol="NVDA",
        as_of=bars[-1].timestamp,
        minute_bars=bars,
    )

    result = IntradayEngine().analyze(context)

    assert result.verdict is AnalysisVerdict.INSUFFICIENT_DATA
    assert result.direction is PatternDirection.NEUTRAL
    assert result.score == Decimal("0")
    assert _metric(result, "setup") == "insufficient_data"
    assert "invalidation_level" not in {metric.name for metric in result.metrics}


def test_optional_five_minute_confirmation_is_reported() -> None:
    minute_bars = trend_bars(
        symbol="AMD",
        start=Decimal("160"),
        step=Decimal("0.04"),
        final_move=Decimal("0.60"),
        base_volume=Decimal("1000"),
        final_volume=Decimal("2600"),
    )
    five_minute_bars = trend_bars(
        symbol="AMD",
        start=Decimal("155"),
        step=Decimal("0.12"),
        final_move=Decimal("0.12"),
        base_volume=Decimal("5000"),
        final_volume=Decimal("5000"),
        count=20,
        timeframe=BarTimeframe.MINUTE_5,
    )
    context = IntradayContext(
        symbol="AMD",
        as_of=max(minute_bars[-1].timestamp, five_minute_bars[-1].timestamp),
        minute_bars=minute_bars,
        five_minute_bars=five_minute_bars,
    )

    result = IntradayEngine().analyze(context)

    assert _metric(result, "five_minute_bias") == "bullish"
    assert result.confidence > Decimal("0.5")


def test_v2_reports_regime_and_confirmation_quality_without_replacing_v1() -> None:
    minute_bars = trend_bars(
        symbol="AMD",
        start=Decimal("160"),
        step=Decimal("0.04"),
        final_move=Decimal("0.60"),
        base_volume=Decimal("1000"),
        final_volume=Decimal("2600"),
    )
    five_minute_bars = trend_bars(
        symbol="AMD",
        start=Decimal("155"),
        step=Decimal("0.12"),
        final_move=Decimal("0.30"),
        base_volume=Decimal("5000"),
        final_volume=Decimal("7000"),
        count=20,
        timeframe=BarTimeframe.MINUTE_5,
    )
    context = IntradayContext(
        symbol="AMD",
        as_of=max(minute_bars[-1].timestamp, five_minute_bars[-1].timestamp),
        minute_bars=minute_bars,
        five_minute_bars=five_minute_bars,
    )

    legacy = IntradayEngine().analyze(context)
    result = IntradayEngineV2().analyze(context)

    assert legacy.engine_version == "1.0.0"
    assert result.engine_version == "2.0.0"
    assert _metric(result, "intraday_regime") == "bullish_trend"
    assert _metric(result, "confirmation_quality") in {"standard", "strong"}
    assert Decimal(str(_metric(result, "close_location"))) >= Decimal("0.60")
    assert Decimal(str(_metric(result, "volume_acceleration"))) > Decimal("1")
    assert _metric(result, "five_minute_higher_low") is True
