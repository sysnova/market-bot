from decimal import Decimal
from pathlib import Path

import fakeredis
import pytest

from app.common.clock import FrozenClock
from app.contracts import (
    ENTRY_SIGNAL_EVENT,
    LOCAL_ALERT_EVENT,
    ORDER_FLOW_STATE_EVENT,
    OrderFlowStateKind,
)
from app.integration.deferred_short_runtime import DeferredShortRuntime, RedisShortStateStore
from app.integration.engine_assembly import MarketBotAssembly
from app.integration.tests.test_deferred_short_runtime import Publisher, envelope
from app.leveraged_thesis_engine.tests.test_engine import NOW, _flow
from app.leveraged_thesis_engine.tests.test_v11 import alert
from app.leveraged_thesis_engine.v11 import LeveragedThesisEngineV11

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("quotes", [False, True])
async def test_daily_regime_confirmation_persists_without_creating_etf_paper_trade(
    quotes: bool,
) -> None:
    engine = MarketBotAssembly.from_path(
        ROOT / "configs/marketbot/7.80.0.yaml"
    ).build_leveraged_thesis()
    assert isinstance(engine, LeveragedThesisEngineV11)
    publisher = Publisher()
    store = RedisShortStateStore(fakeredis.FakeRedis(), "test:")
    runtime = DeferredShortRuntime(engine, publisher, store, FrozenClock(NOW))
    await runtime.handle(envelope(alert(), LOCAL_ALERT_EVENT))
    await runtime.handle(
        envelope(_flow("ASTS", OrderFlowStateKind.NEUTRAL), ORDER_FLOW_STATE_EVENT)
    )
    flow = _flow(
        "ASTN",
        OrderFlowStateKind.BUY_PRESSURE,
        bid=Decimal("5") if quotes else None,
        ask=Decimal("6") if quotes else None,
    )
    await runtime.handle(envelope(flow, ORDER_FLOW_STATE_EVENT))
    saved = await store.get("ASTS")
    assert saved.assessment is not None and saved.assessment.state.value == "BUY_CONFIRMED"
    assert saved.assessment.instrument_confirmation_basis == "BUYER_REGIME"
    count = len(publisher.events)
    await DeferredShortRuntime(engine, publisher, store, FrozenClock(NOW)).restore()
    assert len(publisher.events) == count
    assert not [e for e in publisher.events if e.event_type == ENTRY_SIGNAL_EVENT]
