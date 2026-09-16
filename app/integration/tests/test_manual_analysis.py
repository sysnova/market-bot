import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, patch

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
from app.integration.manual_analysis import ManualEvents, analyze_in_process


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
async def test_direct_engines_run_without_nats_and_return_current_support(tmp_path: Path) -> None:
    """No distributed service, broker history, or persistent decision write is used."""
    database = AsyncMock()
    portfolio = AsyncMock()
    portfolio.get_holding_quantity.return_value = Decimal(1)
    portfolio.get_portfolio_allocations.return_value = ()
    with (
        patch("app.integration.manual_analysis.create_database_engine", return_value=database),
        patch("app.integration.manual_analysis.PostgresUniverseClient", return_value=portfolio),
        patch("app.integration.live_composition.run_live_analysis", new_callable=AsyncMock) as core,
        patch("app.integration.manual_analysis._build_rest", return_value=AsyncMock()),
        patch(
            "app.integration.manual_analysis.MarketHistoryLoader.ensure_and_load",
            new_callable=AsyncMock,
        ) as history,
        patch(
            "app.integration.market_rotation_store.PostgresMarketRotationStore.load_profiles",
            new_callable=AsyncMock,
            return_value=(),
        ),
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
        report = await analyze_in_process("TEST", timeout_seconds=5, runtime_root=tmp_path)
    nats.assert_not_called()
    assert core.call_args.kwargs["isolated"] is True
    assert core.call_args.kwargs["mirror_to_nats"] is False
    assert report["transport"] == "DIRECT_ISOLATED"
    results = {item["engine"]: item for item in report["engines"]}
    assert results["core"]["status"] == "COMPLETED"
    assert results["support-confirmation"]["status"] == "COMPLETED"
    assert results["support-confirmation"]["result"]["events"]
    assert results["long-portfolio"]["status"] == "SKIPPED"
    assert results["signal-fusion"]["status"] == "COMPLETED"
    database.dispose.assert_awaited_once()


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
