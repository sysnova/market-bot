from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.contracts import (
    AlertKind,
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    NamedValue,
    PatternDirection,
)
from app.long_portfolio_engine import (
    LongPortfolioEngine,
    LongPortfolioPolicy,
    LongPortfolioState,
    PortfolioAllocation,
)

NOW = datetime(2026, 7, 31, 20, tzinfo=UTC)


def _policy() -> LongPortfolioPolicy:
    return LongPortfolioPolicy(
        rule_version="1.0.0",
        horizon_end="2026-12-31",
        portfolio_capital_usd=Decimal("103000"),
        cash_weight_percent=Decimal("11.20"),
        reserved_weight_percent=Decimal("13.07"),
        allocations=(PortfolioAllocation(symbol="HIMS", weight_percent=Decimal("11.73")),),
        minimum_score=Decimal("72"),
        minimum_confidence=Decimal("0.68"),
        minimum_setup_score=Decimal("68"),
        minimum_entry_score=Decimal("65"),
        minimum_trend_template_score=Decimal("75"),
        minimum_qualified_sessions=2,
        initial_tranche_percent=Decimal("50"),
        maximum_signal_age=timedelta(days=3),
        cooldown=timedelta(days=14),
        alert_ttl=timedelta(days=5),
        allowed_market_regimes=("clean_uptrend",),
        blocked_risk_flags=("weekly_distribution",),
    )


def _analysis(symbol: str = "HIMS", *, as_of: datetime = NOW) -> AnalysisResult:
    return AnalysisResult(
        engine_id="long-term",
        engine_version="2.0.0",
        symbol=symbol,
        horizon=AnalysisHorizon.LONG_TERM,
        as_of=as_of,
        verdict=AnalysisVerdict.FAVORABLE,
        direction=PatternDirection.BULLISH,
        score=Decimal("82"),
        confidence=Decimal("0.80"),
        reasons=("trend_template_passed",),
        metrics=(
            NamedValue(name="classification", value="buy_zone"),
            NamedValue(name="reference_price", value=Decimal("50")),
            NamedValue(name="buy_zone_low", value=Decimal("48")),
            NamedValue(name="buy_zone_high", value=Decimal("51")),
            NamedValue(name="invalidation", value=Decimal("43")),
            NamedValue(name="setup_score", value=Decimal("80")),
            NamedValue(name="entry_score", value=Decimal("75")),
            NamedValue(name="trend_template_score", value=Decimal("87.5")),
            NamedValue(name="market_regime", value="clean_uptrend"),
            NamedValue(name="risk_flags", value=()),
        ),
        context_hash="sha256:" + "1" * 64,
    )


def test_requires_two_distinct_qualified_sessions_and_sizes_first_tranche() -> None:
    engine = LongPortfolioEngine(_policy())

    assert engine.ingest(_analysis(as_of=NOW - timedelta(days=1)), now=NOW) is None
    alert = engine.ingest(_analysis(), now=NOW)

    assert alert is not None
    assert alert.kind is AlertKind.LONG_PORTFOLIO_BUY
    metrics = {item.name: item.value for item in alert.metrics}
    assert metrics["target_capital_usd"] == Decimal("12081.90")
    assert metrics["suggested_tranche_usd"] == Decimal("6040.95")
    assert metrics["suggested_whole_shares"] == Decimal("120")


def test_ignores_excluded_unconfigured_and_non_long_results() -> None:
    engine = LongPortfolioEngine(_policy())

    assert engine.ingest(_analysis("ANF"), now=NOW) is None
    assert engine.ingest(
        _analysis().model_copy(update={"horizon": AnalysisHorizon.SWING}), now=NOW
    ) is None


def test_resets_confirmation_when_a_session_fails_the_solid_entry_gate() -> None:
    engine = LongPortfolioEngine(_policy())
    assert engine.ingest(_analysis(as_of=NOW - timedelta(days=1)), now=NOW) is None
    weak = _analysis().model_copy(update={"score": Decimal("60")})

    assert engine.ingest(weak, now=NOW) is None
    assert engine.ingest(_analysis(), now=NOW) is None


def test_sizes_only_the_gap_remaining_after_current_holdings() -> None:
    engine = LongPortfolioEngine(_policy())
    assert engine.ingest(_analysis(as_of=NOW - timedelta(days=1)), now=NOW) is None

    alert = engine.ingest(_analysis(), now=NOW, held_quantity=Decimal("200"))

    assert alert is not None
    metrics = {item.name: item.value for item in alert.metrics}
    assert metrics["current_holding_value_usd"] == Decimal("10000.00")
    assert metrics["remaining_to_target_usd"] == Decimal("2081.90")
    assert metrics["suggested_tranche_usd"] == Decimal("2081.90")
    assert metrics["suggested_whole_shares"] == Decimal("41")


def test_does_not_emit_buy_when_current_holding_reaches_target() -> None:
    engine = LongPortfolioEngine(_policy())
    assert engine.ingest(_analysis(as_of=NOW - timedelta(days=1)), now=NOW) is None

    assert (
        engine.ingest(_analysis(), now=NOW, held_quantity=Decimal("300")) is None
    )


def test_restores_previous_qualified_session_without_replaying_nats() -> None:
    restored = LongPortfolioState(
        symbol="HIMS",
        rule_version="1.0.0",
        qualified_sessions=((NOW - timedelta(days=1)).date(),),
        last_emitted=None,
        updated_at=NOW - timedelta(days=1),
    )
    engine = LongPortfolioEngine(_policy(), restored_states=(restored,))

    alert = engine.ingest(_analysis(), now=NOW)

    assert alert is not None
    state = engine.state_for("HIMS", updated_at=NOW)
    assert state is not None
    assert state.qualified_sessions == ((NOW - timedelta(days=1)).date(), NOW.date())
