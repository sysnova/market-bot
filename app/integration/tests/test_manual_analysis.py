import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.contracts import (
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    BarTimeframe,
    EventEnvelope,
    MarketBar,
    PatternDirection,
    UniverseChanged,
)
from app.integration.engine_assembly import MarketBotAssembly
from app.integration.manual_analysis import ManualEvents, analyze_in_process


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 9, 16, tzinfo=UTC)


def geri_regular_bars() -> tuple[MarketBar, ...]:
    values = [
        (9, 11, 10),
        (8, 10, 9),
        (9, 14, 13),
        (12, 18, 17),
        (14, 17, 15),
        (13, 16, 14),
        (14, 17, 16),
        (Decimal("12.8"), 16, 14),
    ]
    bars = []
    for index, (low, high, close) in enumerate(values):
        start = datetime(2026, 7, 20, 13, 30, tzinfo=UTC) + timedelta(
            days=index // 2, hours=4 * (index % 2)
        )
        for minute in range(16 if index % 2 == 0 else 10):
            bars.append(
                MarketBar(
                    symbol="TEST",
                    timeframe=BarTimeframe.MINUTE_15,
                    timestamp=start + timedelta(minutes=15 * minute),
                    open=Decimal(close),
                    high=Decimal(high),
                    low=Decimal(low),
                    close=Decimal(close),
                    volume=Decimal(1000),
                    source="test",
                    feed="iex",
                    is_final=True,
                )
            )
    return tuple(bars)


@pytest.mark.unit
async def test_private_event_collector_rejects_universe_commands() -> None:
    collector = ManualEvents()
    change = UniverseChanged(
        occurred_at=datetime(2026, 9, 16, tzinfo=UTC),
        source="manual-symbols",
        previous_symbols=(),
        symbols=("IBM",),
        added_symbols=("IBM",),
        removed_symbols=(),
    )
    with pytest.raises(RuntimeError, match="cannot change"):
        await collector.publish(
            "marketbot.v1.universe.changed.core",
            EventEnvelope(
                event_type="universe.changed",
                occurred_at=change.occurred_at,
                source="manual",
                subject="core",
                payload=change,
            ),
        )
    assert collector.events == []


@pytest.mark.unit
@pytest.mark.parametrize("with_geri_history", [True, False])
async def test_direct_entry_engines_run_without_portfolio_or_nats(
    tmp_path: Path,
    with_geri_history: bool,
) -> None:
    """No distributed service, broker history, or persistent decision write is used."""
    assembly = MarketBotAssembly.from_path(Path("configs/marketbot/7.77.0.yaml"))
    geri = assembly.build_4hgeri()
    analyze_geri = Mock(wraps=geri.analyze)
    with (
        patch("app.integration.manual_analysis.SystemClock", return_value=FixedClock()),
        patch(
            "app.integration.manual_analysis.MarketBotAssembly.from_settings", return_value=assembly
        ),
        patch.object(type(geri), "analyze", analyze_geri),
        patch.object(MarketBotAssembly, "build_4hgeri", return_value=geri),
        patch(
            "app.persistence.create_database_engine",
            side_effect=AssertionError("manual analysis opened PostgreSQL"),
        ) as database,
        patch("app.integration.live_composition.run_live_analysis", new_callable=AsyncMock) as core,
        patch("app.integration.manual_analysis._build_rest", return_value=AsyncMock()),
        patch(
            "app.integration.manual_analysis.MarketHistoryLoader.ensure_and_load",
            new_callable=AsyncMock,
        ) as history,
        patch(
            "app.event_bus.NatsJetStreamEventBus.connect",
            side_effect=AssertionError("manual analysis connected to NATS"),
        ) as nats,
    ):
        analysis = AnalysisResult(
            engine_id="swing",
            engine_version="16.0.0",
            symbol="TEST",
            horizon=AnalysisHorizon.SWING,
            as_of=datetime(2026, 9, 16, tzinfo=UTC),
            verdict=AnalysisVerdict.WATCH,
            direction=PatternDirection.NEUTRAL,
            score=Decimal(50),
            confidence=Decimal("0.5"),
            reasons=("manual_fixture",),
            context_hash="sha256:" + "a" * 64,
        )
        core.return_value = {"analyses": [analysis.model_dump(mode="json")], "symbols": ["TEST"]}
        history.return_value = tuple(
            MarketBar(
                symbol="TEST",
                timeframe=BarTimeframe.DAY_1,
                timestamp=datetime(2026, 6, 1, tzinfo=UTC) + timedelta(days=index),
                open=Decimal(100 + index),
                high=Decimal(102 + index),
                low=Decimal(99 + index),
                close=Decimal(101 + index),
                volume=Decimal(100000),
                source="test",
                feed="iex",
                is_final=True,
            )
            for index in range(60)
        )
        if with_geri_history:
            history.return_value += geri_regular_bars()
        report = await analyze_in_process("TEST", timeout_seconds=5, runtime_root=tmp_path)
    nats.assert_not_called()
    assert core.call_args.kwargs["isolated"] is True
    assert core.call_args.kwargs["mirror_to_nats"] is False
    assert report["transport"] == "DIRECT_ISOLATED"
    results = {item["engine"]: item for item in report["engines"]}
    assert list(results) == ["core", "support-confirmation", "4hgeri"]
    assert results["core"]["status"] == "COMPLETED"
    assert results["support-confirmation"]["status"] == "COMPLETED"
    assert results["support-confirmation"]["result"]["events"]
    assert report["execution_enabled"] is False
    assert report["universe_modified"] is False
    database.assert_not_called()
    if with_geri_history:
        assert results["4hgeri"]["status"] == "COMPLETED"
        events = results["4hgeri"]["result"]["events"]
        assessment = next(e["payload"] for e in events if e["event_type"] == "4hgeri.assessed")
        assert assessment["symbol"] == "TEST"
        assert assessment["zone_low"] == "12.8"
        assert assessment["zone_high"] == "17"
        assert assessment["four_hour_confirmation"] is False
        context = analyze_geri.call_args.args[0]
        assert len(context.bars) == 8
        assert all(b.timeframe is BarTimeframe.HOUR_4 for b in context.bars)
        support = results["support-confirmation"]["result"]["events"][0]["payload"]
        assert str(context.support.assessment_id) == support["assessment_id"]
        assert all(e["event_type"] in {"4hgeri.assessed", "4hgeri.transitioned"} for e in events)
    else:
        assert results["4hgeri"]["status"] == "SKIPPED"
        assert results["4hgeri"]["reason"] == "no_4hgeri_assessment_from_completed_regular_bars"


@pytest.mark.unit
async def test_isolated_core_never_connects_to_nats_or_persistent_decision_store(
    tmp_path: Path,
) -> None:
    from app.integration.live_composition import run_live_analysis

    data = AsyncMock()
    data.publish_bars.return_value = 0
    data.publish_snapshots.return_value = 0
    with (
        patch(
            "app.integration.live_composition.build_alpaca_market_data_engine", return_value=data
        ),
        patch(
            "app.integration.live_composition.create_database_engine",
            side_effect=AssertionError("persistent decision store"),
        ) as database,
        patch(
            "app.event_bus.NatsJetStreamEventBus.connect",
            side_effect=AssertionError("operational NATS"),
        ) as nats,
    ):
        result = await run_live_analysis(
            once=True,
            runtime_root=tmp_path,
            bell=False,
            mirror_to_nats=True,
            symbols=("IBM",),
            include_analyses=True,
            isolated=True,
        )
    assert result is not None
    assert result["nats_mirroring"] is False
    assert result["alert_path"] is None
    assert await asyncio.to_thread(lambda: list(tmp_path.iterdir())) == []
    nats.assert_not_called()
    database.assert_not_called()
