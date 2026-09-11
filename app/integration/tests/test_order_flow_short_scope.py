from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from app.alert_engine.tests.test_v39 import NOW, _intraday, _swing
from app.contracts import EntryOpportunityStatus
from app.entry_opportunity_engine import InMemoryEntryOpportunityStore
from app.entry_opportunity_engine.tests.test_short_v14 import NOW as ENTRY_NOW
from app.entry_opportunity_engine.tests.test_short_v14 import alert, bar
from app.entry_opportunity_engine.v14 import EntryOpportunityEngineV14
from app.entry_opportunity_engine.v15 import EntryOpportunityEngineV15
from app.integration.engine_assembly import EngineMode, EngineSlot, MarketBotAssembly

DEFINITION = Path("configs/marketbot/7.58.0.yaml")


@pytest.mark.parametrize("symbol", ["ASTS", "ASTX", "ASTN", "NBIS", "NBIZ", "AAPL"])
def test_short_confirmation_uses_order_flow_scope(symbol: str) -> None:
    assembly = MarketBotAssembly.from_path(DEFINITION)
    engine = assembly.build_alert()
    engine.ingest(_swing().model_copy(update={"symbol": symbol}), now=NOW)
    result = engine.ingest(_intraday().model_copy(update={"symbol": symbol}), now=NOW)
    confirmed = result is not None and "short_entry_confirmed" in result.reasons
    assert confirmed == (symbol in {"ASTS", "NBIS"})


@pytest.mark.asyncio
@pytest.mark.parametrize("symbol", ["ASTS", "ASTX", "ASTN", "NBIS", "NBIZ", "AAPL"])
async def test_only_underlyings_can_open_short_opportunities(symbol: str) -> None:
    assembly = MarketBotAssembly.from_path(DEFINITION)
    store = InMemoryEntryOpportunityStore()
    entries = assembly.build(EngineSlot.ENTRY_OPPORTUNITY, store=store, now=lambda: ENTRY_NOW)
    assert isinstance(entries, EntryOpportunityEngineV15)
    opened = await entries.ingest_alert(alert().model_copy(update={"symbol": symbol}))
    assert bool(opened) == (symbol in {"ASTS", "NBIS"})


@pytest.mark.asyncio
async def test_unlisted_short_cannot_open_but_existing_short_still_closes() -> None:
    assembly = MarketBotAssembly.from_path(DEFINITION)
    store = InMemoryEntryOpportunityStore()
    engine = assembly.build_entry_opportunity(store=store)
    assert isinstance(engine, EntryOpportunityEngineV15)
    engine._now = lambda: ENTRY_NOW  # pyright: ignore[reportPrivateUsage]
    outside = alert().model_copy(update={"symbol": "AAPL"})
    assert await engine.ingest_alert(outside) == ()
    assert await store.load_active("AAPL") is None
    # An old short remains managed even when the new scope excludes its symbol.
    old_engine = EntryOpportunityEngineV14(store=store, now=lambda: ENTRY_NOW)
    assert await old_engine.ingest_alert(outside)
    events = await engine.ingest_bar(
        bar(low="96", high="100", close="97").model_copy(update={"symbol": "AAPL"})
    )
    assert events[0].opportunity.status is EntryOpportunityStatus.CLOSED


def test_inactive_order_flow_disables_short_confirmations() -> None:
    assembly = MarketBotAssembly.from_path(DEFINITION)
    specs = dict(assembly.definition.engines)
    specs[EngineSlot.ORDER_FLOW] = replace(specs[EngineSlot.ORDER_FLOW], mode=EngineMode.ON_DEMAND)
    assembly = MarketBotAssembly(replace(assembly.definition, engines=specs))
    engine = assembly.build_alert()
    engine.ingest(_swing(), now=NOW)
    result = engine.ingest(_intraday(), now=NOW)
    assert result is None or "short_entry_confirmed" not in result.reasons


@pytest.mark.asyncio
async def test_changed_order_flow_scope_controls_alerts_and_entries(tmp_path: Path) -> None:
    assembly = MarketBotAssembly.from_path(DEFINITION)
    policy = yaml.safe_load(assembly.strategy_artifact(EngineSlot.ORDER_FLOW).read_text())
    policy["tracked_symbols"] = ["NBIS", "ASTX", "ASTN", "AAPL"]
    path = tmp_path / "order-flow.yaml"
    path.write_text(yaml.safe_dump(policy))
    specs = dict(assembly.definition.engines)
    spec = specs[EngineSlot.ORDER_FLOW]
    specs[EngineSlot.ORDER_FLOW] = replace(spec, strategy=replace(spec.strategy, artifact=path))
    assembly = MarketBotAssembly(replace(assembly.definition, engines=specs))
    for symbol, expected in (
        ("NBIS", True),
        ("ASTS", False),
        ("ASTX", False),
        ("ASTN", False),
        ("AAPL", False),
    ):
        alerts = assembly.build_alert()
        alerts.ingest(_swing().model_copy(update={"symbol": symbol}), now=NOW)
        result = alerts.ingest(_intraday().model_copy(update={"symbol": symbol}), now=NOW)
        assert (result is not None and "short_entry_confirmed" in result.reasons) == expected
        store = InMemoryEntryOpportunityStore()
        # The generic build path must apply the same scope as the typed wrappers.
        entries = assembly.build(EngineSlot.ENTRY_OPPORTUNITY, store=store, now=lambda: ENTRY_NOW)
        assert isinstance(entries, EntryOpportunityEngineV15)
        opened = await entries.ingest_alert(alert().model_copy(update={"symbol": symbol}))
        assert bool(opened) == expected


def test_new_definition_only_changes_short_owners() -> None:
    previous = MarketBotAssembly.from_path(Path("configs/marketbot/7.57.0.yaml"))
    current = MarketBotAssembly.from_path(DEFINITION)
    for slot in current.definition.engines:
        if slot not in {EngineSlot.ALERT, EngineSlot.ENTRY_OPPORTUNITY}:
            assert current.spec(slot) == previous.spec(slot)
