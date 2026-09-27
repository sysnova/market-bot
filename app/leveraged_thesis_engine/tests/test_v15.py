from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.contracts import OrderFlowStateKind
from app.integration.engine_assembly import EngineSlot, MarketBotAssembly
from app.leveraged_thesis_engine.tests.test_v14 import _context, _quote
from app.leveraged_thesis_engine.v11 import PricePoint
from app.leveraged_thesis_engine.v14 import TacticalState
from app.leveraged_thesis_engine.v15 import LeveragedThesisEngineV15


@pytest.mark.parametrize(
    "kind", [OrderFlowStateKind.BUY_PRESSURE, OrderFlowStateKind.BUY_ABSORPTION]
)
def test_buyer_regime_needs_no_etf_quote_levels_or_extra_quality_threshold(
    kind: OrderFlowStateKind,
) -> None:
    context = _context()
    assert context.instrument_flow is not None
    context = replace(
        context,
        instrument_quote=None,
        instrument_analysis=None,
        instrument_flow=context.instrument_flow.model_copy(
            update={"state": kind, "confidence": Decimal(".50"), "data_quality": Decimal(".50")}
        ),
    )
    _, report = LeveragedThesisEngineV15().observe_tactical(TacticalState(), context)
    assert report.status == "READY"
    assert report.orders_enabled is False
    assert report.instrument_reward_risk is None
    assert {g.name for g in report.gates if g.name.startswith("instrument")} == {
        "instrument_confirmation"
    }


@pytest.mark.parametrize(
    "change",
    [
        None,
        {"state": OrderFlowStateKind.SELL_PRESSURE},
        {"symbol": "ASTS"},
        {"age": 5},
        {"age": -1},
    ],
)
def test_only_current_matching_buyer_flow_can_confirm(change: dict[str, object] | None) -> None:
    context = _context()
    flow = context.instrument_flow
    assert flow is not None
    updates = dict(change or {})
    age = updates.pop("age", None)
    if age is not None:
        assert isinstance(age, int)
        updates["occurred_at"] = context.now - timedelta(seconds=age)
    context = replace(context, instrument_flow=flow.model_copy(update=updates) if change else None)
    _, report = LeveragedThesisEngineV15().observe_tactical(TacticalState(), context)
    assert report.status == "BLOCKED"
    assert next(g for g in report.gates if g.name == "instrument_confirmation").passed is False


def test_new_definition_selects_flow_only_observer() -> None:
    root = Path(__file__).resolve().parents[3]
    assembly = MarketBotAssembly.from_path(root / "configs/marketbot/7.79.0.yaml")
    assert assembly.spec(EngineSlot.LEVERAGED_THESIS).implementation == "1.5.0"
    assert isinstance(assembly.build(EngineSlot.LEVERAGED_THESIS), LeveragedThesisEngineV15)


def test_etf_price_rise_cannot_replace_missing_buyer_regime() -> None:
    context = replace(_context(), instrument_flow=None)
    state = TacticalState(
        prices=(PricePoint(at=context.now - timedelta(minutes=3), price=Decimal("16")),)
    )
    state, report = LeveragedThesisEngineV15().observe_tactical(state, context)
    assert report.status == "BLOCKED"
    assert state.prices == ()


def test_wide_stale_etf_quote_and_bad_etf_levels_do_not_veto_buyer_regime() -> None:
    context = _context()
    quote = _quote("ASTN", "15", "20")
    old = context.now - timedelta(minutes=5)
    quote = quote.model_copy(
        update={
            "quote": quote.quote.model_copy(update={"occurred_at": old, "received_at": old}),
            "published_at": old,
        }
    )
    _, report = LeveragedThesisEngineV15().observe_tactical(
        TacticalState(), replace(context, instrument_quote=quote)
    )
    assert report.status == "READY"
