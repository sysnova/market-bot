"""The integrated release retains candidate support and production entry geometry."""

from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.common.settings import AppSettings
from app.contracts import SwingTradeMaturity
from app.integration import swing_trade_composition
from app.integration.engine_assembly import MarketBotAssembly
from app.swing_trade_engine.tests.test_v17 import append_bar, context_at, values
from app.swing_trade_engine.tests.test_v19 import recovered_support


def test_latest_assembly_accepts_local_retest_with_valid_frozen_entry() -> None:
    engine = MarketBotAssembly.from_path(Path("configs/marketbot/7.71.0.yaml")).build_swing_trade()
    result = engine.analyze(recovered_support())
    assert result.maturity is SwingTradeMaturity.ST3
    assert values(result)["rebound_support_source"] == "LOCAL_RETEST"
    assert result.entry_zone_low == result.entry_zone_high == Decimal("101.9")
    assert result.entry_invalidation == values(result)["operational_stop"]
    assert result.entry_invalidation < result.entry_zone_low


@pytest.mark.parametrize("definition", ["7.68.0", "7.70.0"])
def test_upgrade_from_candidate_or_production_preserves_entry_and_exit(definition: str) -> None:
    previous_engine = MarketBotAssembly.from_path(
        Path("configs/marketbot") / f"{definition}.yaml"
    ).build_swing_trade()
    ctx = recovered_support() if definition == "7.68.0" else context_at(7)
    if definition == "7.70.0":
        last = ctx.confirmation_bars[-1].model_copy(update={"close": Decimal("101.5")})
        ctx = replace(
            ctx, confirmation_bars=(*ctx.confirmation_bars[:-1], last), current_price=last.close
        )
    previous = previous_engine.analyze(ctx)
    assert previous.maturity is SwingTradeMaturity.ST3
    engine = MarketBotAssembly.from_path(Path("configs/marketbot/7.71.0.yaml")).build_swing_trade()
    result = engine.analyze(
        replace(append_bar(ctx, "96", high="97", low="95"), previous_assessment=previous)
    )
    assert "rebound_stop_breached" in result.reasons
    assert result.entry_invalidation == values(previous)["operational_stop"]
    assert result.entry_zone_low == Decimal(str(values(previous)["rebound_entry_price"]))


@pytest.mark.asyncio
@pytest.mark.parametrize("definition", ["7.70.0", "7.71.0"])
async def test_latest_startup_loads_full_momentum_history(
    monkeypatch: pytest.MonkeyPatch, definition: str
) -> None:
    settings = AppSettings(
        _env_file=None, definition_path=Path(f"configs/marketbot/{definition}.yaml")
    )
    monkeypatch.setattr(swing_trade_composition, "AppSettings", lambda: settings)
    database = AsyncMock()
    bus = AsyncMock()
    history = AsyncMock(return_value=())
    monkeypatch.setattr(swing_trade_composition, "create_database_engine", lambda *a, **k: database)
    monkeypatch.setattr(swing_trade_composition, "connect_nats", AsyncMock(return_value=bus))
    monkeypatch.setattr(swing_trade_composition, "load_market_history", history)
    summary = await swing_trade_composition.run_swing_trade_process(once=True, symbols=("SE",))
    assert summary is not None
    assert summary["marketbot_definition_version"] == definition
    assert history.call_args.kwargs["requirements"] == (
        swing_trade_composition.SWING_TRADE_MOMENTUM_HISTORY_REQUESTS
    )
    assert summary["places_orders"] is False
    database.dispose.assert_awaited_once()
    bus.close.assert_awaited_once()
