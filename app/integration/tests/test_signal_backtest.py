import json
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.common.settings import AppSettings
from app.contracts import (
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    BarTimeframe,
    GeriAssessment,
    GeriMaturity,
    MarketBar,
    NamedValue,
    PatternDirection,
    SwingTradeAssessment,
    SwingTradeMaturity,
    SwingTradeTransition,
    TradeSide,
    market_bar_subject,
    new_uuid7,
)
from app.event_bus import InMemoryEventBus
from app.integration.signal_backtest import (
    SignalBacktestConfig,
    _ingest_opportunity_then_publish_bar,
    _swing_model_confirmation_summary,
    _swing_trade_outcomes,
    _three_swing_model_comparison,
    load_backtest_market_data,
    replay_bars_at_cadence,
    run_signal_backtest,
)

ROOT = Path(__file__).resolve().parents[3]


def _raw_bar(timestamp: str, *, close: str = "101", volume: int = 100) -> dict[str, object]:
    return {
        "t": timestamp,
        "o": "100",
        "h": "102",
        "l": "99",
        "c": close,
        "v": volume,
        "n": 7,
        "vw": "100.5",
    }


class _FakeRest:
    def __init__(self, records: dict[str, dict[str, list[Mapping[str, object]]]]) -> None:
        self.records = records
        self.calls: list[dict[str, object]] = []

    async def fetch_bars(
        self,
        symbols: tuple[str, ...],
        *,
        timeframe: str,
        start: datetime,
        end: datetime,
        limit: int = 10_000,
    ) -> dict[str, list[Mapping[str, object]]]:
        self.calls.append(
            {
                "symbols": symbols,
                "timeframe": timeframe,
                "start": start,
                "end": end,
                "limit": limit,
            }
        )
        return self.records.get(timeframe, {})


def _bar(symbol: str, minute: int) -> MarketBar:
    return MarketBar(
        symbol=symbol,
        timeframe=BarTimeframe.MINUTE_1,
        timestamp=datetime(2026, 8, 10, 13, minute, tzinfo=UTC),
        open=Decimal("100"),
        high=Decimal("102"),
        low=Decimal("99"),
        close=Decimal("101"),
        volume=Decimal("100"),
        source="alpaca-backtest",
        feed="iex-backtest",
    )


def test_config_normalizes_symbols_and_defaults_to_one_simulated_share(tmp_path: Path) -> None:
    config = SignalBacktestConfig(
        source_date=date(2026, 8, 5),
        simulated_date=date(2026, 8, 10),
        symbols=(" aapl ", "MSFT", "AAPL"),
        cadence_seconds=0,
        output_path=tmp_path / "result.json",
        run_id="run-42",
    )

    assert config.symbols == ("AAPL", "MSFT")
    assert config.holding_quantities == {
        "AAPL": Decimal("1"),
        "MSFT": Decimal("1"),
    }
    assert config.source_end_date == date(2026, 8, 5)
    assert config.simulated_end_date == date(2026, 8, 10)


def test_config_rebases_a_multi_session_window_with_one_constant_delta(
    tmp_path: Path,
) -> None:
    config = SignalBacktestConfig(
        source_date=date(2026, 7, 16),
        source_end_date=date(2026, 8, 21),
        simulated_date=date(2026, 7, 23),
        symbols=("ADUR",),
        output_path=tmp_path / "result.json",
    )

    assert config.simulated_end_date == date(2026, 8, 28)


def test_config_rejects_an_end_before_the_source_start(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="source_end_date"):
        SignalBacktestConfig(
            source_date=date(2026, 8, 5),
            source_end_date=date(2026, 8, 4),
            simulated_date=date(2026, 8, 10),
            symbols=("AAPL",),
            output_path=tmp_path / "result.json",
        )


@pytest.mark.parametrize("cadence", [-1, float("inf"), float("nan")])
def test_config_rejects_invalid_cadence(cadence: float, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="cadence"):
        SignalBacktestConfig(
            source_date=date(2026, 8, 5),
            simulated_date=date(2026, 8, 10),
            symbols=("AAPL",),
            cadence_seconds=cadence,
            output_path=tmp_path / "result.json",
        )


@pytest.mark.asyncio
async def test_backtest_marks_opportunity_before_fanning_out_the_same_bar() -> None:
    calls: list[str] = []

    class RecordingOpportunity:
        async def ingest_bar(self, bar: MarketBar) -> tuple[object, ...]:
            calls.append(f"opportunity:{bar.symbol}")
            return ()

    bus = InMemoryEventBus(retain_history=False, synchronous_delivery=True)

    async def record_market_bar(envelope: object) -> None:
        calls.append("market")

    await bus.subscribe(market_bar_subject(BarTimeframe.MINUTE_1, "AAPL"), record_market_bar)
    try:
        await _ingest_opportunity_then_publish_bar(
            RecordingOpportunity(),  # type: ignore[arg-type]
            bus,
            _bar("AAPL", 30),
        )
        await bus.join()
    finally:
        await bus.close()

    assert calls == ["opportunity:AAPL", "market"]


@pytest.mark.asyncio
async def test_cadence_waits_once_between_market_timestamps_not_between_symbols() -> None:
    bars = (_bar("AAPL", 30), _bar("MSFT", 30), _bar("AAPL", 31))
    handled: list[MarketBar] = []
    sleeps: list[float] = []
    flushes: list[int] = []

    async def handle(bar: MarketBar) -> None:
        handled.append(bar)

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    async def flush() -> None:
        flushes.append(len(handled))

    await replay_bars_at_cadence(
        bars,
        cadence_seconds=0.25,
        handle=handle,
        sleep=sleep,
        flush=flush,
    )

    assert handled == list(bars)
    assert sleeps == [0.25]
    assert flushes == [2, 3]


def test_swing_trade_outcome_excludes_the_bar_that_created_the_transition() -> None:
    occurred_at = datetime(2026, 8, 10, 13, 30, tzinfo=UTC)
    transition = SwingTradeTransition(
        assessment_id=new_uuid7(),
        symbol="AAPL",
        occurred_at=occurred_at,
        engine_version="1.1.0",
        strategy_version="1.1.0",
        maturity=SwingTradeMaturity.ST3,
        current_price=Decimal("100"),
        zone_low=Decimal("99"),
        zone_high=Decimal("101"),
        invalidation=Decimal("98"),
        primary_target=Decimal("103"),
        reward_risk=Decimal("1.5"),
        eligible=True,
        reasons=("entry_trigger_confirmed",),
        context_hash=f"sha256:{'b' * 64}",
    )
    signal_bar = _bar("AAPL", 30).model_copy(
        update={
            "timestamp": occurred_at,
            "high": Decimal("104"),
            "low": Decimal("99"),
        }
    )
    next_bar = signal_bar.model_copy(
        update={
            "timestamp": occurred_at + timedelta(minutes=1),
            "high": Decimal("102"),
        }
    )

    outcomes = _swing_trade_outcomes(
        (transition,),
        {"AAPL": (signal_bar, next_bar)},
    )

    assert outcomes[0]["observed_bars"] == 1
    assert outcomes[0]["first_level_hit"] is None


def test_three_swing_comparison_includes_latest_swing_trade_assessment() -> None:
    occurred_at = datetime(2026, 8, 10, 17, 30, tzinfo=UTC)
    daily = AnalysisResult.model_construct(
        engine_id="swing",
        symbol="ADUR",
        horizon=AnalysisHorizon.SWING,
        as_of=occurred_at - timedelta(minutes=2),
        verdict=AnalysisVerdict.FAVORABLE,
        direction=PatternDirection.BULLISH,
        score=Decimal("75"),
        metrics=(
            NamedValue(name="swing_entry_gate_passed", value=True),
            NamedValue(name="entry_lane", value="STRUCTURE_RECOVERY"),
            NamedValue(name="classification", value="recovery"),
            NamedValue(name="invalidation", value="13.57"),
            NamedValue(name="structural_invalidation", value="11.63"),
            NamedValue(name="reward_risk_to_resistance", value="2.97"),
        ),
    )
    geri = GeriAssessment.model_construct(
        symbol="ADUR",
        occurred_at=occurred_at,
        zone_low=Decimal("13.00"),
        zone_high=Decimal("13.60"),
        current_swing_zone_low=Decimal("12.90"),
        current_swing_zone_high=Decimal("13.70"),
        active_level_sequence=3,
        active_level_price=Decimal("13.30"),
        maturity=GeriMaturity.L2_4H,
        trade_side=TradeSide.LONG,
    )
    trade = SwingTradeAssessment.model_construct(
        symbol="ADUR",
        occurred_at=occurred_at - timedelta(minutes=1),
        maturity=SwingTradeMaturity.ST3,
        eligible=True,
        zone_low=Decimal("13.00"),
        zone_high=Decimal("13.70"),
        invalidation=Decimal("12.80"),
        primary_target=Decimal("16.50"),
        reward_risk=Decimal("2.1"),
    )

    rows = _three_swing_model_comparison((daily,), (geri,), (trade,))

    latest = rows[-1]
    assert latest["daily_swing_entry_lane"] == "STRUCTURE_RECOVERY"
    assert latest["daily_swing_classification"] == "recovery"
    assert latest["daily_swing_structural_invalidation"] == "11.63"
    assert latest["daily_swing_reward_risk"] == "2.97"
    assert latest["4hgeri_maturity"] == "L2_4H"
    assert latest["swing_trade_maturity"] == "ST3"
    assert latest["swing_trade_eligible"] is True
    assert latest["swing_trade_primary_target"] == "16.50"


def test_swing_confirmation_summary_distinguishes_buy_verdict_from_entry_gate() -> None:
    as_of = datetime(2026, 8, 10, 19, 45, tzinfo=UTC)
    results = (
        AnalysisResult.model_construct(
            engine_id="swing",
            symbol="ADUR",
            horizon=AnalysisHorizon.SWING,
            as_of=as_of - timedelta(days=1),
            verdict=AnalysisVerdict.FAVORABLE,
            direction=PatternDirection.BULLISH,
            reasons=("warmup",),
            metrics=(NamedValue(name="swing_entry_gate_passed", value=True),),
        ),
        AnalysisResult.model_construct(
            engine_id="swing",
            symbol="ADUR",
            horizon=AnalysisHorizon.SWING,
            as_of=as_of,
            verdict=AnalysisVerdict.WATCH,
            direction=PatternDirection.BULLISH,
            reasons=("invalidation_risk_too_wide", "entry_zone_distance_atr:0.25"),
            metrics=(
                NamedValue(name="swing_entry_gate_passed", value=False),
                NamedValue(
                    name="risk_flags",
                    value=["structural_invalidation_risk_too_wide"],
                ),
            ),
        ),
        AnalysisResult.model_construct(
            engine_id="swing",
            symbol="ADUR",
            horizon=AnalysisHorizon.SWING,
            as_of=as_of + timedelta(days=1),
            verdict=AnalysisVerdict.FAVORABLE,
            direction=PatternDirection.BULLISH,
            reasons=("entry_gate_passed",),
            metrics=(
                NamedValue(name="swing_entry_gate_passed", value=True),
                NamedValue(name="reference_price", value="13.72"),
                NamedValue(name="entry_lane", value="STRUCTURE_RECOVERY"),
                NamedValue(name="invalidation", value="13.10"),
                NamedValue(name="structural_invalidation", value="11.63"),
                NamedValue(name="reward_risk_to_resistance", value="2.75"),
            ),
        ),
    )

    summary = _swing_model_confirmation_summary(
        results,
        (),
        (),
        window_start=as_of,
        window_end=as_of + timedelta(days=2),
    )

    daily = summary["swing_daily"]
    assert daily["assessment_count"] == 2
    assert daily["session_count"] == 2
    assert daily["favorable_verdict_count"] == 1
    assert daily["entry_gate_passed_count"] == 1
    assert daily["confirmed_buy_count"] == 1
    assert daily["entry_lane_counts"] == {"STRUCTURE_RECOVERY": 1}
    assert daily["gate_failure_reason_counts"] == {
        "entry_zone_distance_atr": 1,
        "invalidation_risk_too_wide": 1,
    }
    assert daily["risk_flag_counts"] == {"structural_invalidation_risk_too_wide": 1}
    assert daily["confirmed_buys"] == [
        {
            "as_of": "2026-08-11T19:45:00+00:00",
            "reference_price": "13.72",
            "verdict": "FAVORABLE",
            "entry_lane": "STRUCTURE_RECOVERY",
            "invalidation": "13.10",
            "structural_invalidation": "11.63",
            "reward_risk_to_resistance": "2.75",
        }
    ]


@pytest.mark.asyncio
async def test_history_is_selected_only_cut_before_open_and_rebased_without_lookahead() -> None:
    records = {
        "1Day": {
            "AAPL": [
                _raw_bar("2026-08-04T04:00:00Z"),
                _raw_bar("2026-08-05T04:00:00Z", volume=999),
            ],
            "UNREQUESTED": [_raw_bar("2026-08-04T04:00:00Z")],
        },
        "1Min": {
            "AAPL": [
                _raw_bar("2026-08-04T19:59:00Z"),
                _raw_bar("2026-08-05T13:30:00Z", volume=321),
            ]
        },
    }
    rest = _FakeRest(records)
    config = SignalBacktestConfig(
        source_date=date(2026, 8, 5),
        simulated_date=date(2026, 8, 10),
        symbols=("AAPL",),
        cadence_seconds=0,
        output_path=Path("unused.json"),
    )

    data = await load_backtest_market_data(rest, config=config, feed="iex")

    assert all(call["symbols"] == ("AAPL",) for call in rest.calls)
    assert all(bar.symbol == "AAPL" for bar in (*data.warmup_bars, *data.session_bars))
    daily = [bar for bar in data.warmup_bars if bar.timeframe is BarTimeframe.DAY_1]
    assert len(daily) == 1
    assert daily[0].close == Decimal("101")
    assert daily[0].timestamp == datetime(2026, 8, 9, 4, tzinfo=UTC)
    assert [(bar.timestamp, bar.volume) for bar in data.session_bars] == [
        (datetime(2026, 8, 10, 13, 30, tzinfo=UTC), Decimal("321"))
    ]


@pytest.mark.asyncio
async def test_history_replays_every_session_in_the_requested_window() -> None:
    records = {
        "1Day": {"ADUR": [_raw_bar("2026-08-04T04:00:00Z")]},
        "1Min": {
            "ADUR": [
                _raw_bar("2026-08-05T13:32:00Z", volume=100),
                _raw_bar("2026-08-06T13:31:00Z", volume=200),
                _raw_bar("2026-08-07T13:30:00Z", volume=300),
            ]
        },
    }
    config = SignalBacktestConfig(
        source_date=date(2026, 8, 5),
        source_end_date=date(2026, 8, 6),
        simulated_date=date(2026, 8, 10),
        symbols=("ADUR",),
        output_path=Path("unused.json"),
    )

    data = await load_backtest_market_data(_FakeRest(records), config=config, feed="sip")

    assert [(bar.timestamp, bar.volume) for bar in data.session_bars] == [
        (datetime(2026, 8, 10, 13, 32, tzinfo=UTC), Decimal("100")),
        (datetime(2026, 8, 11, 13, 31, tzinfo=UTC), Decimal("200")),
    ]


def test_backtest_composition_has_no_operational_infrastructure_imports() -> None:
    source = Path("app/integration/signal_backtest.py").read_text(encoding="utf-8")

    for forbidden in (
        "NatsJetStreamEventBus",
        "connect_nats",
        "create_database_engine",
        "Postgres",
        "database_url",
        "nats_url",
    ):
        assert forbidden not in source


@pytest.mark.asyncio
async def test_full_backtest_stays_in_memory_and_writes_a_local_artifact(
    tmp_path: Path,
) -> None:
    source_date = date(2026, 8, 5)
    source_open = datetime(2026, 8, 5, 13, 30, tzinfo=UTC)

    def series(count: int, *, step: timedelta, end: datetime) -> list[dict[str, object]]:
        return [
            _raw_bar((end - step * (count - index)).isoformat().replace("+00:00", "Z"))
            for index in range(count)
        ]

    previous_minutes = series(100, step=timedelta(minutes=1), end=source_open)
    session_minutes = [
        _raw_bar(
            (source_open + timedelta(minutes=index)).isoformat().replace("+00:00", "Z"),
            volume=100 + index,
        )
        for index in range(5)
    ]
    rest = _FakeRest(
        {
            "1Day": {"AAPL": series(100, step=timedelta(days=1), end=source_open)},
            "1Week": {"AAPL": series(30, step=timedelta(days=7), end=source_open)},
            "1Hour": {"AAPL": series(100, step=timedelta(hours=1), end=source_open)},
            "15Min": {"AAPL": series(80, step=timedelta(minutes=15), end=source_open)},
            "1Min": {"AAPL": [*previous_minutes, *session_minutes]},
        }
    )
    output = tmp_path / "synthetic.json"

    report = await run_signal_backtest(
        SignalBacktestConfig(
            source_date=source_date,
            simulated_date=date(2026, 8, 10),
            symbols=("AAPL",),
            cadence_seconds=0,
            output_path=output,
            run_id="synthetic",
        ),
        settings=AppSettings(definition_path=(ROOT / "configs/marketbot/7.23.0.yaml")),
        rest=rest,
    )

    assert report["bars_replayed"] == 5
    assert report["marketbot_definition_version"] == "7.23.0"
    assert report["transport"] == "in-memory"
    assert report["persistence"] == "in-memory"
    assert report["operational_nats_used"] is False
    assert report["operational_database_used"] is False
    assert report["fusion_transitions"]
    assert "swing_results" in report
    assert "4hgeri_assessments" in report
    assert "4hgeri_transitions" in report
    assert "4hgeri_outcomes" in report
    assert "swing_trade_assessments" in report
    assert "swing_trade_transitions" in report
    assert "swing_trade_outcomes" in report
    assert isinstance(report["swing_trade_diagnostics"], dict)
    assert "confirmed_entry_signals" in report
    assert isinstance(report["confirmed_signal_counts"], dict)
    assert "three_swing_model_comparison" in report
    assert "swing_model_confirmation_summary" in report
    assert report["solid_buy_outcomes"] == []
    evidence = report["opportunity_evidence_audit"]
    assert isinstance(evidence, dict)
    assert evidence["sample"]["opportunities"] == len(report["opportunities"])
    assert json.loads(output.read_text(encoding="utf-8"))["run_id"] == "synthetic"
