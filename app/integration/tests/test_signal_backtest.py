import json
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.common.settings import AppSettings
from app.contracts import (
    BarTimeframe,
    MarketBar,
    SwingChannelMaturity,
    SwingChannelTransition,
    market_bar_subject,
    new_uuid7,
)
from app.event_bus import InMemoryEventBus
from app.integration.signal_backtest import (
    SignalBacktestConfig,
    _ingest_opportunity_then_publish_bar,
    _swing_channel_outcomes,
    load_backtest_market_data,
    replay_bars_at_cadence,
    run_signal_backtest,
)


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


def test_swing_channel_outcomes_measure_each_maturity_from_its_own_reference() -> None:
    occurred_at = datetime(2026, 8, 10, 13, 30, tzinfo=UTC)
    transition = SwingChannelTransition(
        assessment_id=new_uuid7(),
        symbol="AAPL",
        occurred_at=occurred_at,
        engine_version="1.0.0",
        maturity=SwingChannelMaturity.IN_ZONE_4H,
        current_price=Decimal("100"),
        support=Decimal("100"),
        zone_low=Decimal("99"),
        zone_high=Decimal("101"),
        invalidation=Decimal("98"),
        reasons=("projected_support_touched",),
        context_hash=f"sha256:{'a' * 64}",
    )
    bars = tuple(
        _bar("AAPL", 30 + index).model_copy(
            update={
                "timestamp": occurred_at + timedelta(minutes=index),
                "high": Decimal("103"),
                "low": Decimal("98"),
                "close": Decimal("102") if index >= 14 else Decimal("101"),
            }
        )
        for index in range(20)
    )

    outcomes = _swing_channel_outcomes((transition,), {"AAPL": bars})

    assert outcomes[0]["maturity"] == "IN_ZONE_4H"
    assert outcomes[0]["mfe_percent"] == "3.00"
    assert outcomes[0]["mae_percent"] == "-2.00"
    assert outcomes[0]["return_15m"] == "2.00"
    assert outcomes[0]["return_30m"] is None


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
            "1Day": {
                "AAPL": series(100, step=timedelta(days=1), end=source_open)
            },
            "1Week": {
                "AAPL": series(30, step=timedelta(days=7), end=source_open)
            },
            "1Hour": {
                "AAPL": series(100, step=timedelta(hours=1), end=source_open)
            },
            "15Min": {
                "AAPL": series(80, step=timedelta(minutes=15), end=source_open)
            },
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
        settings=AppSettings(),
        rest=rest,
    )

    assert report["bars_replayed"] == 5
    assert report["transport"] == "in-memory"
    assert report["persistence"] == "in-memory"
    assert report["operational_nats_used"] is False
    assert report["operational_database_used"] is False
    assert report["fusion_transitions"]
    assert "swing_channel_4h_transitions" in report
    assert "swing_channel_4h_outcomes" in report
    assert "swing_channel_4h_vs_swing" in report
    assert "swing_results" in report
    assert "4hgeri_assessments" in report
    assert "4hgeri_transitions" in report
    assert "4hgeri_outcomes" in report
    assert "three_swing_model_comparison" in report
    assert report["solid_buy_outcomes"] == []
    evidence = report["opportunity_evidence_audit"]
    assert isinstance(evidence, dict)
    assert evidence["sample"]["opportunities"] == len(report["opportunities"])
    assert json.loads(output.read_text(encoding="utf-8"))["run_id"] == "synthetic"
