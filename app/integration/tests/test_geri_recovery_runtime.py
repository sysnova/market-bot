from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from app.contracts import BarTimeframe, NamedValue
from app.integration.bar_aggregator import MinuteBarAggregator, RegularSessionDailyFifteenAggregator
from app.integration.engine_assembly import MarketBotAssembly
from app.integration.swing_4h_geri_composition import _countertrend_signal
from app.swing_4h_geri_engine.tests.test_recovery import context
from app.swing_4h_geri_engine.v19 import Swing4HGeriEngineV19

ROOT = Path(__file__).resolve().parents[3]


def test_final_fifteen_closes_without_next_session_or_afterhours_bar() -> None:
    aggregate = MinuteBarAggregator(targets=(BarTimeframe.MINUTE_15,), emit_on_complete=True)
    start = datetime(2026, 9, 4, 19, 45, tzinfo=UTC)
    template = context().confirmation_bars[0]
    output = []
    for i in range(15):
        output.extend(
            aggregate.add(
                template.model_copy(
                    update={
                        "timeframe": BarTimeframe.MINUTE_1,
                        "timestamp": start + timedelta(minutes=i),
                    }
                )
            )
        )
    assert len(output) == 1
    assert output[0].timestamp == start
    assert output[0].volume == template.volume * 15


def test_incomplete_minute_bucket_cannot_be_used_as_final_confirmation() -> None:
    aggregate = MinuteBarAggregator(targets=(BarTimeframe.MINUTE_15,), emit_on_complete=True)
    start = datetime(2026, 9, 4, 19, 45, tzinfo=UTC)
    template = context().confirmation_bars[0]
    output = []
    for i in (0, 1, 14, 15):
        output.extend(
            aggregate.add(
                template.model_copy(
                    update={
                        "timeframe": BarTimeframe.MINUTE_1,
                        "timestamp": start + timedelta(minutes=i),
                    }
                )
            )
        )
    assert output == []


def test_old_minute_cannot_erase_a_newer_pending_confirmation() -> None:
    aggregate = MinuteBarAggregator(targets=(BarTimeframe.MINUTE_15,), emit_on_complete=True)
    start = datetime(2026, 9, 4, 19, 45, tzinfo=UTC)
    template = context().confirmation_bars[0]
    output = []
    for i in (*range(8), -15, *range(8, 15)):
        output.extend(
            aggregate.add(
                template.model_copy(
                    update={
                        "timeframe": BarTimeframe.MINUTE_1,
                        "timestamp": start + timedelta(minutes=i),
                    }
                )
            )
        )
    assert len(output) == 1


def test_daily_requires_all_twenty_six_completed_fifteen_minute_bars() -> None:
    aggregate = RegularSessionDailyFifteenAggregator()
    start = datetime(2026, 9, 4, 13, 30, tzinfo=UTC)
    template = context().confirmation_bars[0]
    result = None
    for i in range(26):
        result = aggregate.add(
            template.model_copy(update={"timestamp": start + timedelta(minutes=15 * i)})
        )
        if i < 25:
            assert result is None
    assert result is not None and result.timeframe is BarTimeframe.DAY_1


def test_projection_rejects_geometry_even_if_engine_claims_eligible() -> None:
    item = Swing4HGeriEngineV19().analyze(context())
    good = _countertrend_signal(item)
    assert good is not None and good.countertrend_maturity.value == "CT2"
    bad = item.model_copy(
        update={
            "metrics": tuple(
                NamedValue(name=m.name, value=Decimal("50"))
                if m.name == "countertrend_target"
                else m
                for m in item.metrics
            )
        }
    )
    signal = _countertrend_signal(bad)
    assert signal is not None and signal.countertrend_maturity is None


def test_new_assembly_keeps_prior_version_available_for_rollback() -> None:
    root = Path(__file__).resolve().parents[3]
    new = MarketBotAssembly.from_path(root / "configs/marketbot/7.51.0.yaml")
    old = MarketBotAssembly.from_path(root / "configs/marketbot/7.50.0.yaml")
    assert new.build_4hgeri().engine_version == "1.9.0"
    assert old.build_4hgeri().engine_version == "1.8.0"


async def test_recovery_runtime_to_opportunity_opens_only_after_ct2_acceptance() -> None:
    from app.common.clock import FrozenClock
    from app.contracts import ENTRY_SIGNAL_EVENT, EntryLegStatus, EventEnvelope
    from app.entry_opportunity_engine import InMemoryEntryOpportunityStore
    from app.integration.swing_4h_geri_composition import Swing4HGeriRuntime

    assembly = MarketBotAssembly.from_path(ROOT / "configs/marketbot/7.51.0.yaml")
    store = InMemoryEntryOpportunityStore()
    opportunity = assembly.build_entry_opportunity(store=store)

    class Publisher:
        async def publish(self, subject: str, envelope: EventEnvelope) -> None:
            if envelope.event_type == ENTRY_SIGNAL_EVENT:
                await opportunity.ingest_signal(envelope.payload)

    start = datetime(2026, 7, 22, 13, 30, tzinfo=UTC)
    clock = FrozenClock(start)
    runtime = Swing4HGeriRuntime(
        engine=assembly.build_4hgeri(),
        publisher=Publisher(),
        clock=clock,
        emit_countertrend_signals=True,
    )
    c = context()
    await runtime.bootstrap(c.bars, symbols=("HUT",))
    for i, raw in enumerate(c.confirmation_bars):
        bar = raw.model_copy(update={"timestamp": start + timedelta(minutes=15 * i)})
        clock.advance(timedelta(minutes=15))
        await runtime._accept_fifteen(bar)
        active = await store.load_active("HUT")
        assert active is not None
        if i < 2:
            assert active.legs[0].status is EntryLegStatus.WATCHING
            assert active.checkpoints == ()
        else:
            assert active.legs[0].status is EntryLegStatus.OPEN
            assert active.legs[0].entry_price == bar.close
            assert active.checkpoints[0].countertrend_maturity.value == "CT2"
