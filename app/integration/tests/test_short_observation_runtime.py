from datetime import timedelta
from pathlib import Path

from app.common.clock import FrozenClock
from app.contracts import (
    ANALYSIS_RESULT_EVENT,
    EXECUTION_QUOTE_EVENT,
    ORDER_FLOW_STATE_EVENT,
    SHORT_OBSERVATION_EVENT,
    EventEnvelope,
    ShortObservation,
)
from app.integration.engine_assembly import MarketBotAssembly
from app.integration.short_observation_runtime import ShortObservationRuntime
from app.leveraged_thesis_engine.tests.test_v14 import NOW, _context
from app.leveraged_thesis_engine.v14 import LeveragedThesisEngineV14

ROOT = Path(__file__).resolve().parents[3]


class Recorder:
    def __init__(self) -> None:
        self.events: list[EventEnvelope] = []

    async def publish(self, subject: str, envelope: EventEnvelope) -> None:
        assert ".short-observation." in subject
        self.events.append(envelope)


async def test_shadow_runtime_publishes_only_diagnostics_and_expires_quotes() -> None:
    recorder = Recorder()
    clock = FrozenClock(NOW)
    runtime = ShortObservationRuntime(LeveragedThesisEngineV14(), recorder, clock=clock)
    c = _context()
    for analysis in (c.swing, c.intraday, c.instrument_analysis):
        assert analysis is not None
        await runtime.handle(
            EventEnvelope(
                event_type=ANALYSIS_RESULT_EVENT, source="test", occurred_at=NOW, payload=analysis
            )
        )
    for quote in (c.underlying_quote, c.instrument_quote):
        assert quote is not None
        await runtime.handle(
            EventEnvelope(
                event_type=EXECUTION_QUOTE_EVENT, source="test", occurred_at=NOW, payload=quote
            )
        )
    assert recorder.events
    assert all(e.event_type == SHORT_OBSERVATION_EVENT for e in recorder.events)
    count = len(recorder.events)
    await runtime.tick()
    assert len(recorder.events) == count
    clock.advance(timedelta(seconds=3))
    await runtime.tick()
    payload = recorder.events[-1].payload
    assert isinstance(payload, ShortObservation)
    assert any(g.name == "instrument_quote" and not g.passed for g in payload.gates)


def test_new_assembly_is_opt_in_and_previous_engine_is_retained() -> None:
    root = Path(__file__).resolve().parents[3]
    new = MarketBotAssembly.from_path(root / "configs/marketbot/7.78.0.yaml")
    old = MarketBotAssembly.from_path(root / "configs/marketbot/7.77.0.yaml")
    assert new.build_leveraged_thesis().engine_version == "1.4.0"
    assert new.build_order_flow().engine_version == "1.3.0"
    assert old.build_leveraged_thesis().engine_version == "1.3.0"


async def test_observation_intent_survives_restart_outside_disposable_views() -> None:
    import fakeredis

    from app.integration.short_observation_runtime import RedisTacticalStateStore
    from app.leveraged_thesis_engine.v14 import TacticalState

    redis = fakeredis.FakeRedis()
    store = RedisTacticalStateStore(redis, "test:")
    engine = LeveragedThesisEngineV14()
    state, report = engine.observe_tactical(TacticalState(), _context())
    await store.put("ASTS", state)
    runtime = ShortObservationRuntime(engine, Recorder(), clock=FrozenClock(NOW), store=store)
    await runtime.restore()
    assert runtime.states["ASTS"].intent.setup_id == report.setup_id
    assert not list(redis.scan_iter("test:view:*"))


async def test_future_analysis_cannot_poison_monotonic_cache() -> None:
    runtime = ShortObservationRuntime(
        LeveragedThesisEngineV14(), Recorder(), clock=FrozenClock(NOW)
    )
    analysis = _context().intraday
    assert analysis is not None
    await runtime.handle(
        EventEnvelope(
            event_type=ANALYSIS_RESULT_EVENT,
            source="test",
            occurred_at=NOW,
            payload=analysis.model_copy(update={"as_of": NOW + timedelta(minutes=5)}),
        )
    )
    await runtime.handle(
        EventEnvelope(
            event_type=ANALYSIS_RESULT_EVENT, source="test", occurred_at=NOW, payload=analysis
        )
    )
    assert runtime.analyses[("ASTS", "INTRADAY")].as_of == analysis.as_of


async def test_flow_only_assembly_reaches_ready_without_etf_quotes_or_analysis() -> None:
    engine = MarketBotAssembly.from_path(
        ROOT / "configs/marketbot/7.79.0.yaml"
    ).build_leveraged_thesis()
    assert isinstance(engine, LeveragedThesisEngineV14)
    recorder = Recorder()
    runtime = ShortObservationRuntime(engine, recorder, clock=FrozenClock(NOW))
    c = _context()
    for event_type, payload in (
        (ANALYSIS_RESULT_EVENT, c.swing),
        (ANALYSIS_RESULT_EVENT, c.intraday),
        (EXECUTION_QUOTE_EVENT, c.underlying_quote),
        (ORDER_FLOW_STATE_EVENT, c.instrument_flow),
    ):
        assert payload is not None
        await runtime.handle(
            EventEnvelope(event_type=event_type, source="test", occurred_at=NOW, payload=payload)
        )
    report = recorder.events[-1].payload
    assert isinstance(report, ShortObservation)
    assert report.route == "SHORT_TACTICAL" and report.status == "READY"
    assert report.orders_enabled is False
    assert "ASTN" not in runtime.quotes
    assert ("ASTN", "INTRADAY") not in runtime.analyses
