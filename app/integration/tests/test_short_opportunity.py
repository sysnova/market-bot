from decimal import Decimal
from pathlib import Path

import pytest

from app.common.settings import AppSettings
from app.entry_opportunity_engine import InMemoryEntryOpportunityStore
from app.entry_opportunity_engine.tests.test_short_v14 import NOW, alert, bar
from app.integration.engine_assembly import MarketBotAssembly
from app.integration.entry_opportunity_monitor import (
    OpportunityDashboard,
    format_opportunity_dashboard,
)
from app.integration.entry_opportunity_report import build_entry_opportunity_report
from app.integration.entry_opportunity_store import _to_domain, _to_record
from app.opportunity_dashboard.projection import build_dashboard_snapshot


@pytest.mark.asyncio
async def test_alert_to_durable_short_to_visible_positive_pnl() -> None:
    store = InMemoryEntryOpportunityStore()
    engine = MarketBotAssembly.from_settings(
        AppSettings(definition_path=Path("configs/marketbot/7.55.0.yaml"))
    ).build_entry_opportunity(store=store)
    from app.entry_opportunity_engine.v14 import EntryOpportunityEngineV14

    assert isinstance(engine, EntryOpportunityEngineV14)
    engine._now = lambda: NOW  # pyright: ignore[reportPrivateUsage]
    await engine.ingest_alert(alert())
    await engine.ingest_bar(bar(low="98", high="100", close="99"))
    current = await store.load_active("ASTS")
    assert current is not None
    assert _to_domain(_to_record(current)) == current
    dashboard = OpportunityDashboard(history=10)
    dashboard.merge(current)
    rendered = format_opportunity_dashboard(dashboard, refreshed_at=NOW, color=False)
    assert "SHORT" in rendered
    assert "+1.00" in rendered
    report = build_entry_opportunity_report((current,))
    assert report["open_trades"][0]["trade_side"] == "SHORT"
    snapshot = build_dashboard_snapshot((current,), refreshed_at=NOW)
    row = snapshot["rows"][0]
    assert row["entry_kind"] == "SHORT"
    assert Decimal(row["pnl_percent"]) == Decimal("1")
