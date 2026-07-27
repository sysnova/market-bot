from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.contracts import (
    AlertSeverity,
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    BarTimeframe,
    LocalAlert,
    MarketBar,
    NamedValue,
    PatternDirection,
)

NOW = datetime(2026, 7, 26, 14, 31, tzinfo=UTC)
HASH = "sha256:" + "a" * 64


def market_bar() -> MarketBar:
    return MarketBar(
        symbol="AAPL",
        timeframe=BarTimeframe.MINUTE_1,
        timestamp=NOW,
        open=Decimal("210.10"),
        high=Decimal("211.25"),
        low=Decimal("209.90"),
        close=Decimal("211.00"),
        volume=Decimal("12500"),
        trade_count=340,
        vwap=Decimal("210.73"),
        source="alpaca",
        feed="sip",
    )


def test_market_bar_preserves_exact_values_and_validates_ohlc() -> None:
    bar = market_bar()

    assert bar.close == Decimal("211.00")
    assert bar.timeframe is BarTimeframe.MINUTE_1
    with pytest.raises(ValidationError, match="high must be greater"):
        MarketBar(
            **{
                **bar.model_dump(),
                "high": Decimal("208"),
            }
        )


def test_analysis_result_requires_reasons_and_unique_metrics() -> None:
    analysis = AnalysisResult(
        engine_id="long-term-engine",
        engine_version="1.0.0",
        symbol="AAPL",
        horizon=AnalysisHorizon.LONG_TERM,
        as_of=NOW,
        verdict=AnalysisVerdict.FAVORABLE,
        direction=PatternDirection.BULLISH,
        score=Decimal("82.5"),
        confidence=Decimal("0.78"),
        reasons=("Weekly trend remains constructive",),
        metrics=(NamedValue(name="weekly_rsi", value="61.4"),),
        context_hash=HASH,
    )

    assert analysis.analysis_id.version == 7
    assert analysis.score == Decimal("82.5")
    with pytest.raises(ValidationError, match="metrics must be unique"):
        AnalysisResult(
            **{
                **analysis.model_dump(),
                "metrics": (
                    NamedValue(name="weekly_rsi", value="61.4"),
                    NamedValue(name="weekly_rsi", value="62.1"),
                ),
            }
        )


def test_local_alert_links_component_analyses_without_order_intent() -> None:
    analysis = AnalysisResult(
        engine_id="intraday-engine",
        engine_version="1.0.0",
        symbol="AAPL",
        horizon=AnalysisHorizon.INTRADAY,
        as_of=NOW,
        verdict=AnalysisVerdict.WATCH,
        direction=PatternDirection.BULLISH,
        score=Decimal("76"),
        confidence=Decimal("0.72"),
        reasons=("Price reclaimed VWAP with relative volume",),
        context_hash=HASH,
    )

    alert = LocalAlert(
        symbol="AAPL",
        created_at=NOW,
        severity=AlertSeverity.WATCH,
        title="AAPL intraday setup",
        message="Reclaimed VWAP with increasing volume.",
        horizons=(AnalysisHorizon.INTRADAY,),
        component_analysis_ids=(analysis.analysis_id,),
        score=analysis.score,
        reasons=analysis.reasons,
        component_analyses=(analysis,),
        deduplication_key="AAPL:INTRADAY:VWAP_RECLAIM",
        expires_at=NOW + timedelta(minutes=5),
    )

    assert alert.alert_id.version == 7
    assert alert.component_analyses == (analysis,)
    assert "order" not in alert.model_dump()
    with pytest.raises(ValidationError, match="expires_at"):
        LocalAlert(**{**alert.model_dump(), "expires_at": NOW - timedelta(seconds=1)})
