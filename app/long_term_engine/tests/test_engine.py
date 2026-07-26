from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.contracts import AnalysisHorizon, AnalysisVerdict, BarTimeframe
from app.long_term_engine import (
    LongTermClassification,
    LongTermContext,
    LongTermEngine,
    MarketBar,
)

AS_OF = datetime(2026, 1, 1, tzinfo=UTC)
FIXTURES = Path(__file__).parents[1] / "fixtures" / "scenarios.json"


def _bars(
    *,
    count: int,
    start: Decimal,
    step: Decimal,
    volume: int,
    spacing: timedelta,
) -> tuple[MarketBar, ...]:
    first = AS_OF - spacing * count
    result: list[MarketBar] = []
    for index in range(count):
        close = start + step * index
        result.append(
            MarketBar(
                symbol="TEST",
                timeframe=(
                    BarTimeframe.DAY_1
                    if spacing == timedelta(days=1)
                    else BarTimeframe.WEEK_1
                ),
                timestamp=first + spacing * index,
                open=close - step / Decimal("3"),
                high=close + Decimal("1"),
                low=close - Decimal("1"),
                close=close,
                volume=Decimal(volume + index * 100),
                source="fixture",
                feed="fixture",
            )
        )
    return tuple(result)


def _context(case: dict[str, Any]) -> LongTermContext:
    daily = case["daily"]
    weekly = case["weekly"]
    return LongTermContext(
        symbol="TEST",
        as_of=AS_OF,
        price=Decimal(case["price"]),
        daily_bars=_bars(
            count=daily["count"],
            start=Decimal(daily["start"]),
            step=Decimal(daily["step"]),
            volume=daily["volume"],
            spacing=timedelta(days=1),
        ),
        weekly_bars=_bars(
            count=weekly["count"],
            start=Decimal(weekly["start"]),
            step=Decimal(weekly["step"]),
            volume=weekly["volume"],
            spacing=timedelta(days=7),
        ),
    )


@pytest.mark.unit
@pytest.mark.parametrize("case", json.loads(FIXTURES.read_text(encoding="utf-8")))
def test_fixture_scenarios_have_stable_classification(case: dict[str, Any]) -> None:
    analysis = LongTermEngine().evaluate(_context(case))

    assert analysis.classification.value == case["expected_classification"]
    assert analysis.bias.value == case["expected_bias"]
    assert Decimal("0") <= analysis.score <= Decimal("100")
    assert analysis.reasons


@pytest.mark.unit
def test_constructive_analysis_separates_setup_from_entry_and_emits_levels() -> None:
    case = json.loads(FIXTURES.read_text(encoding="utf-8"))[0]

    analysis = LongTermEngine().evaluate(_context(case))

    assert analysis.classification is LongTermClassification.BUY_ZONE
    assert analysis.setup_score >= Decimal("68")
    assert analysis.entry_score >= Decimal("60")
    assert analysis.levels.buy_zone_low <= analysis.levels.buy_zone_high
    assert analysis.levels.invalidation < analysis.levels.buy_zone_low
    assert "weekly_above_30w" in analysis.reasons
    assert "trend_template_passed" in analysis.reasons


@pytest.mark.unit
def test_insufficient_history_returns_explicit_non_actionable_result() -> None:
    context = LongTermContext(
        symbol="TEST",
        as_of=AS_OF,
        price=Decimal("10"),
        daily_bars=_bars(
            count=199,
            start=Decimal("5"),
            step=Decimal("0.02"),
            volume=1000,
            spacing=timedelta(days=1),
        ),
        weekly_bars=_bars(
            count=49,
            start=Decimal("5"),
            step=Decimal("0.1"),
            volume=5000,
            spacing=timedelta(days=7),
        ),
    )

    analysis = LongTermEngine().evaluate(context)

    assert analysis.classification is LongTermClassification.INSUFFICIENT_DATA
    assert analysis.score == 0
    assert analysis.risk_flags == ("insufficient_history",)


@pytest.mark.unit
def test_analysis_is_deterministic_and_result_is_frozen() -> None:
    case = json.loads(FIXTURES.read_text(encoding="utf-8"))[0]
    context = _context(case)
    engine = LongTermEngine()

    first = engine.evaluate(context)
    second = engine.evaluate(context)

    assert first == second
    with pytest.raises(ValidationError):
        first.score = Decimal("1")  # type: ignore[misc]


@pytest.mark.unit
def test_context_rejects_non_normalized_or_future_bars() -> None:
    bars = _bars(
        count=2,
        start=Decimal("10"),
        step=Decimal("1"),
        volume=1000,
        spacing=timedelta(days=1),
    )

    with pytest.raises(ValidationError, match="strictly chronological"):
        LongTermContext(
            symbol="TEST",
            as_of=AS_OF,
            price=Decimal("11"),
            daily_bars=tuple(reversed(bars)),
            weekly_bars=bars,
        )

    future = MarketBar(
        symbol="TEST",
        timeframe=BarTimeframe.DAY_1,
        timestamp=AS_OF + timedelta(seconds=1),
        open=Decimal("10"),
        high=Decimal("11"),
        low=Decimal("9"),
        close=Decimal("10"),
        volume=Decimal("1000"),
        source="fixture",
        feed="fixture",
    )
    with pytest.raises(ValidationError, match="later than as_of"):
        LongTermContext(
            symbol="TEST",
            as_of=AS_OF,
            price=Decimal("10"),
            daily_bars=(future,),
            weekly_bars=(),
        )


@pytest.mark.unit
def test_bar_rejects_inconsistent_ohlc() -> None:
    with pytest.raises(ValidationError, match="high must be"):
        MarketBar(
            symbol="TEST",
            timeframe=BarTimeframe.DAY_1,
            timestamp=AS_OF,
            open=Decimal("10"),
            high=Decimal("9"),
            low=Decimal("8"),
            close=Decimal("10"),
            volume=Decimal("100"),
            source="fixture",
            feed="fixture",
        )


@pytest.mark.unit
def test_public_output_is_long_term_analysis_contract() -> None:
    case = json.loads(FIXTURES.read_text(encoding="utf-8"))[0]
    context = _context(case)

    result = LongTermEngine().analyze(context)

    assert result.horizon is AnalysisHorizon.LONG_TERM
    assert result.verdict is AnalysisVerdict.FAVORABLE
    assert result.context_hash.startswith("sha256:")
    assert {metric.name for metric in result.metrics} >= {
        "classification",
        "setup_score",
        "entry_score",
        "support",
        "resistance",
    }
