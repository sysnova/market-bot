from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from app.contracts import EntryLegStatus
from app.entry_opportunity_engine import InMemoryEntryOpportunityStore
from app.integration.engine_assembly import MarketBotAssembly
from app.integration.swing_4h_geri_composition import _countertrend_signal
from app.swing_4h_geri_engine.tests.test_recovery_progression import progression_context

ROOT = Path(__file__).resolve().parents[3]


def assembly() -> MarketBotAssembly:
    return MarketBotAssembly.from_path(ROOT / "configs/marketbot/7.52.0.yaml")


def test_assembly_preserves_old_recovery_and_selects_progressive_version() -> None:
    assert assembly().build_4hgeri().engine_version == "1.10.0"
    old = MarketBotAssembly.from_path(ROOT / "configs/marketbot/7.51.0.yaml")
    assert old.build_4hgeri().engine_version == "1.9.0"


def test_reached_resistance_does_not_emit_terminal_target_signal() -> None:
    item = assembly().build_4hgeri().analyze(progression_context(2))
    metrics = {v.name: v.value for v in item.metrics}
    assert metrics["countertrend_maturity"] == "CT2"
    signal = _countertrend_signal(item)
    assert signal is not None and signal.countertrend_maturity is None
    assert "countertrend_target_reached" not in signal.reasons
    assert "resistance_acceptance_pending" in signal.reasons


async def test_new_entry_cannot_rewrite_an_already_open_paper_position() -> None:
    store = InMemoryEntryOpportunityStore()
    opportunity = assembly().build_entry_opportunity(store=store)
    engine = assembly().build_4hgeri()
    first = _countertrend_signal(engine.analyze(progression_context(0)))
    second = _countertrend_signal(engine.analyze(progression_context()))
    assert first is not None and second is not None
    assert first.setup_id != second.setup_id
    await opportunity.ingest_signal(first)
    before = await store.load_active("HUT")
    assert before is not None and before.legs[0].status is EntryLegStatus.OPEN
    await opportunity.ingest_signal(second)
    after = await store.load_active("HUT")
    assert after is not None and after.legs == before.legs
    assert after.checkpoints == before.checkpoints


async def test_rejected_first_entry_can_buy_later_accepted_resistance_once() -> None:
    store = InMemoryEntryOpportunityStore()
    opportunity = assembly().build_entry_opportunity(store=store)
    engine = assembly().build_4hgeri()
    c = progression_context(0)
    # A nearby daily resistance makes the first CT2 economically ineligible.
    daily = tuple(
        b.model_copy(
            update={
                "high": Decimal(high),
                "low": Decimal("97"),
                "open": Decimal("98"),
                "close": Decimal("99"),
            }
        )
        for b, high in zip(c.daily_bars, ("100", "101", "100"), strict=True)
    )
    daily = (
        *tuple(
            b.model_copy(update={"timestamp": b.timestamp - timedelta(days=3)})
            for b in c.daily_bars
        ),
        *daily,
    )
    first = _countertrend_signal(engine.analyze(replace(c, daily_bars=daily)))
    assert first is not None and first.countertrend_maturity is None
    await opportunity.ingest_signal(first)
    assert await store.load_active("HUT") is None
    # Same causal daily history, with a further resistance already known as well.
    c = progression_context()
    second = _countertrend_signal(engine.analyze(replace(c, daily_bars=daily)))
    assert second is not None and second.countertrend_maturity is not None
    assert second.targets == (Decimal("120"),)
    await opportunity.ingest_signal(second)
    opened = await store.load_active("HUT")
    assert opened is not None and opened.legs[0].status is EntryLegStatus.OPEN
    assert opened.legs[0].entry_price == Decimal("112")
    await opportunity.ingest_signal(second)
    assert await store.load_active("HUT") == opened


def test_progression_is_identical_with_restored_prior_assessment() -> None:
    engine = assembly().build_4hgeri()
    initial = engine.analyze(progression_context(0))
    reached = engine.analyze(replace(progression_context(2), active_structure=initial))
    restored = engine.analyze(replace(progression_context(), active_structure=reached))
    cold = engine.analyze(progression_context())
    left, right = ({v.name: v.value for v in a.metrics} for a in (restored, cold))
    for key in (
        "countertrend_maturity",
        "countertrend_target",
        "countertrend_invalidation",
        "countertrend_entry_accepted_at",
        "countertrend_eligible",
    ):
        assert left[key] == right[key]
