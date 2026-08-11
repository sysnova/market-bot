from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.contracts import (
    AnalysisHorizon,
    AnalysisVerdict,
    BarTimeframe,
    MarketBar,
    PatternDirection,
)
from app.swing_engine import (
    SwingClassification,
    SwingContext,
    SwingEngine,
    SwingEngineV2,
    SwingEngineV3,
    SwingEngineV4,
)
from app.swing_engine.indicators import anchored_vwap

AS_OF = datetime(2026, 1, 2, tzinfo=UTC)
FIXTURES = Path(__file__).parents[1] / "fixtures" / "scenarios.json"


def _bars(
    *,
    count: int,
    start: Decimal,
    step: Decimal,
    volume: int,
    timeframe: BarTimeframe,
    spacing: timedelta,
    last_volume_multiplier: Decimal = Decimal("1"),
) -> tuple[MarketBar, ...]:
    first = AS_OF - spacing * count
    bars: list[MarketBar] = []
    for index in range(count):
        close = start + step * index
        bar_volume = Decimal(volume + index * 25)
        if index == count - 1:
            bar_volume *= last_volume_multiplier
        bars.append(
            MarketBar(
                symbol="TEST",
                timeframe=timeframe,
                timestamp=first + spacing * index,
                open=close - step / Decimal("2"),
                high=close + Decimal("0.6"),
                low=close - Decimal("0.6"),
                close=close,
                volume=bar_volume,
                source="fixture",
                feed="fixture",
            )
        )
    return tuple(bars)


def _context(case: dict[str, Any]) -> SwingContext:
    daily = case["daily"]
    intraday = case["intraday"]
    return SwingContext(
        symbol="TEST",
        as_of=AS_OF,
        price=Decimal(case["price"]),
        daily_bars=_bars(
            count=daily["count"],
            start=Decimal(daily["start"]),
            step=Decimal(daily["step"]),
            volume=daily["volume"],
            timeframe=BarTimeframe.DAY_1,
            spacing=timedelta(days=1),
        ),
        intraday_bars=_bars(
            count=intraday["count"],
            start=Decimal(intraday["start"]),
            step=Decimal(intraday["step"]),
            volume=intraday["volume"],
            timeframe=BarTimeframe.MINUTE_15,
            spacing=timedelta(minutes=15),
            last_volume_multiplier=Decimal(intraday.get("last_volume_multiplier", "1")),
        ),
    )


@pytest.mark.unit
@pytest.mark.parametrize("case", json.loads(FIXTURES.read_text(encoding="utf-8")))
def test_fixture_scenarios_are_stable(case: dict[str, Any]) -> None:
    context = _context(case)
    detail = SwingEngine().evaluate(context)
    result = SwingEngine().analyze(context)

    assert detail.classification.value == case["expected_classification"]
    assert result.verdict.value == case["expected_verdict"]
    assert result.horizon is AnalysisHorizon.SWING
    assert result.score == detail.score
    assert result.reasons


@pytest.mark.unit
def test_pullback_emits_atr_bounded_risk_and_levels() -> None:
    case = json.loads(FIXTURES.read_text(encoding="utf-8"))[0]

    detail = SwingEngine().evaluate(_context(case))

    assert detail.classification is SwingClassification.PULLBACK
    assert detail.levels.invalidation < Decimal(case["price"])
    assert detail.levels.target > Decimal(case["price"])
    assert detail.levels.risk_percent <= Decimal("8")
    assert detail.levels.risk_atr <= Decimal("3")
    assert detail.indicators.daily_sma20 > detail.indicators.daily_sma50
    assert "bullish_daily_trend" in detail.reasons
    assert "pullback_near_20d" in detail.reasons


@pytest.mark.unit
def test_breakout_requires_volume_confirmation() -> None:
    case = json.loads(FIXTURES.read_text(encoding="utf-8"))[1]
    confirmed = SwingEngine().evaluate(_context(case))
    no_volume_case = {**case, "intraday": {**case["intraday"], "last_volume_multiplier": "1"}}
    unconfirmed = SwingEngine().evaluate(_context(no_volume_case))

    assert confirmed.classification is SwingClassification.BREAKOUT
    assert confirmed.indicators.intraday_rvol20 >= Decimal("1.2")
    assert unconfirmed.classification is SwingClassification.SETUP
    assert "breakout_without_volume" in unconfirmed.risk_flags


@pytest.mark.unit
def test_anchored_vwap_uses_bar_vwap_and_volume_from_anchor() -> None:
    bars = _bars(
        count=3,
        start=Decimal("10"),
        step=Decimal("1"),
        volume=100,
        timeframe=BarTimeframe.DAY_1,
        spacing=timedelta(days=1),
    )
    bars = tuple(
        bar.model_copy(update={"vwap": vwap, "volume": volume})
        for bar, vwap, volume in zip(
            bars,
            (Decimal("10"), Decimal("20"), Decimal("30")),
            (Decimal("1"), Decimal("2"), Decimal("3")),
            strict=True,
        )
    )

    assert anchored_vwap(bars, anchor_index=1) == Decimal("26.0000")


@pytest.mark.unit
def test_swing_uses_confirmed_pivot_and_breakout_anchored_vwaps() -> None:
    case = json.loads(FIXTURES.read_text(encoding="utf-8"))[1]
    original = _context(case)
    daily = [
        bar.model_copy(update={"volume": Decimal("100"), "vwap": Decimal("95")})
        for bar in original.daily_bars
    ]
    daily[60] = daily[60].model_copy(update={"low": Decimal("70")})
    daily[-1] = daily[-1].model_copy(
        update={
            "open": Decimal("99"),
            "high": Decimal("101"),
            "low": Decimal("98"),
            "close": Decimal("100"),
            "vwap": Decimal("97"),
        }
    )
    context = SwingContext(
        symbol=original.symbol,
        as_of=original.as_of,
        price=Decimal("100"),
        daily_bars=tuple(daily),
        intraday_bars=original.intraday_bars,
    )

    detail = SwingEngine().evaluate(context)
    result = SwingEngine().analyze(context)
    metrics = {metric.name: metric.value for metric in result.metrics}

    assert detail.indicators.pivot_low_anchor_at == daily[60].timestamp
    assert detail.indicators.pivot_low_avwap == Decimal("95.1000")
    assert detail.indicators.price_vs_pivot_low_avwap_percent == Decimal("5.1525")
    assert detail.indicators.breakout_anchor_at == daily[-1].timestamp
    assert detail.indicators.breakout_avwap == Decimal("97.0000")
    assert detail.indicators.price_vs_breakout_avwap_percent == Decimal("3.0928")
    assert detail.levels.invalidation == Decimal("95.5450")
    assert "above_pivot_low_avwap" in detail.reasons
    assert "above_breakout_avwap" in detail.reasons
    assert metrics["pivot_low_avwap"] == Decimal("95.1000")
    assert metrics["breakout_avwap"] == Decimal("97.0000")
    assert metrics["reference_price"] == Decimal("100")


@pytest.mark.unit
def test_insufficient_data_is_explicit_and_non_directional() -> None:
    case = json.loads(FIXTURES.read_text(encoding="utf-8"))[0]
    complete = _context(case)
    context = complete.model_copy(
        update={
            "daily_bars": complete.daily_bars[:49],
            "intraday_bars": complete.intraday_bars[:19],
        }
    )

    detail = SwingEngine().evaluate(context)
    result = SwingEngine().analyze(context)

    assert detail.classification is SwingClassification.INSUFFICIENT_DATA
    assert result.verdict is AnalysisVerdict.INSUFFICIENT_DATA
    assert result.direction is PatternDirection.NEUTRAL
    assert result.score == 0


@pytest.mark.unit
def test_context_rejects_mixed_or_nonfinal_intraday_bars() -> None:
    case = json.loads(FIXTURES.read_text(encoding="utf-8"))[0]
    context = _context(case)
    wrong = context.intraday_bars[-1].model_copy(update={"timeframe": BarTimeframe.MINUTE_5})
    with pytest.raises(ValidationError, match="15Min or 1Hour"):
        SwingContext(
            symbol=context.symbol,
            as_of=context.as_of,
            price=context.price,
            daily_bars=context.daily_bars,
            intraday_bars=(*context.intraday_bars[:-1], wrong),
        )

    updating = context.intraday_bars[-1].model_copy(update={"is_final": False})
    with pytest.raises(ValidationError, match="must be final"):
        SwingContext(
            symbol=context.symbol,
            as_of=context.as_of,
            price=context.price,
            daily_bars=context.daily_bars,
            intraday_bars=(*context.intraday_bars[:-1], updating),
        )


@pytest.mark.unit
def test_result_is_reproducible_except_for_contract_identity() -> None:
    case = json.loads(FIXTURES.read_text(encoding="utf-8"))[0]
    context = _context(case)
    engine = SwingEngine()

    first_detail = engine.evaluate(context)
    second_detail = engine.evaluate(context)
    first = engine.analyze(context)
    second = engine.analyze(context)

    assert first_detail == second_detail
    assert first.context_hash == second.context_hash
    assert first.score == second.score
    with pytest.raises(ValidationError):
        first_detail.score = Decimal("1")  # type: ignore[misc]


@pytest.mark.unit
def test_v2_exposes_regime_entry_zone_and_confirmed_structure_break() -> None:
    case = json.loads(FIXTURES.read_text(encoding="utf-8"))[0]
    context = _context(case)

    legacy = SwingEngine().analyze(context)
    result = SwingEngineV2().analyze(context)
    metrics = {metric.name: metric.value for metric in result.metrics}

    assert legacy.engine_version == "1.1.1"
    assert result.engine_version == "2.0.0"
    assert metrics["market_regime"] == "clean_uptrend"
    assert metrics["structure_broken_confirmed"] is False
    assert metrics["entry_zone_low"] <= metrics["entry_zone_high"]
    assert "price_vs_entry_zone_atr" in metrics
    assert "reward_risk_to_resistance" in metrics


@pytest.mark.unit
def test_v4_keeps_bullish_structure_but_rejects_late_swing_entry_asymmetry() -> None:
    case = json.loads(FIXTURES.read_text(encoding="utf-8"))[0]
    context = _context(case).model_copy(update={"price": Decimal("87")})

    legacy = SwingEngineV3().analyze(context)
    result = SwingEngineV4().analyze(context)
    metrics = {metric.name: metric.value for metric in result.metrics}

    assert legacy.verdict is AnalysisVerdict.FAVORABLE
    assert metrics["classification"] == "pullback"
    assert metrics["reward_risk_to_resistance"] == Decimal("0.7384")
    assert metrics["swing_entry_gate_passed"] is False
    assert result.verdict is AnalysisVerdict.WATCH
    assert result.score == Decimal("64.00")
    assert "insufficient_reward_risk_to_resistance" in result.reasons


@pytest.mark.unit
def test_v4_preserves_actionable_swing_when_resistance_offers_at_least_one_and_half_r() -> None:
    case = json.loads(FIXTURES.read_text(encoding="utf-8"))[0]

    result = SwingEngineV4().analyze(_context(case))
    metrics = {metric.name: metric.value for metric in result.metrics}

    assert result.verdict is AnalysisVerdict.FAVORABLE
    assert metrics["reward_risk_to_resistance"] == Decimal("3.0239")
    assert metrics["swing_entry_gate_passed"] is True
