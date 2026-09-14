from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app.contracts import ENTRY_SIGNAL_EVENT, EntrySignal, NamedValue, SwingTradeMaturity
from app.entry_opportunity_engine import InMemoryEntryOpportunityStore
from app.integration.engine_assembly import MarketBotAssembly
from app.integration.swing_trade_composition import SwingTradeRuntime
from app.integration.tests.test_swing_trade_composition import Publisher
from app.opportunity_dashboard import build_dashboard_snapshot
from app.swing_trade_engine.tests.test_engine import analyze
from app.swing_trade_engine.v110 import SwingTradeEngineV110


@pytest.mark.asyncio
async def test_se_st2_reaches_dashboard_as_valid_reference_without_changing_stop() -> None:
    assembly = MarketBotAssembly.from_path(Path("configs/marketbot/7.70.0.yaml"))
    native = analyze("97").model_copy(
        update={
            "symbol": "SE",
            "maturity": SwingTradeMaturity.ST2,
            "eligible": True,
            "current_price": Decimal("109.69"),
            "zone_low": Decimal("103.3451"),
            "zone_high": Decimal("108.8050"),
            "invalidation": Decimal("104.1820"),
            "support_band_low": Decimal("105.1810"),
            "metrics": (NamedValue(name="setup_id", value="swing-trade:SE:regression"),),
        }
    )
    assessment = SwingTradeEngineV110.entry_geometry(native)
    publisher = Publisher()
    runtime = SwingTradeRuntime(engine=assembly.build_swing_trade(), publisher=publisher)
    await runtime._publish(assessment, None, actionable_signals_enabled=True)
    signal_event = next(e for e in publisher.events if e.event_type == ENTRY_SIGNAL_EVENT)
    signal = EntrySignal.model_validate_json(signal_event.payload.model_dump_json())
    assert signal.invalidation == Decimal("104.1820")
    assert signal.zone_low == Decimal("105.1810")
    assert signal.zone_high == Decimal("108.8050")
    store = InMemoryEntryOpportunityStore()
    manager = assembly.build_entry_opportunity(store=store)
    events = await manager.ingest_signal(signal)
    assert len(events) == 1
    opportunity = events[0].opportunity
    # Same invariant enforced by the unchanged PostgreSQL levels CHECK.
    assert opportunity.invalidation < opportunity.zone_low <= opportunity.zone_high
    snapshot = build_dashboard_snapshot((opportunity,), refreshed_at=datetime.now(UTC))
    row = snapshot["rows"][0]
    assert row["state"] == "ST2"
    assert row["entry_kind"] == "REFERENCE"
    assert Decimal(row["invalidation"]) < Decimal(row["zone_low"])
