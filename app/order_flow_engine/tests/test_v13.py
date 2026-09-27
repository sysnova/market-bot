from datetime import timedelta

from app.order_flow_engine import OrderFlowPolicy
from app.order_flow_engine.tests.test_engine import NOW, _quote
from app.order_flow_engine.v13 import OrderFlowEngineV13


def test_quote_only_snapshot_does_not_need_or_invent_trades() -> None:
    engine = OrderFlowEngineV13(OrderFlowPolicy(tracked_symbols=("AAPL",)))
    snapshot = engine.execution_quote(_quote(), now=NOW)
    assert snapshot is not None
    assert snapshot.quote.bid_price == _quote().bid_price
    assert engine.snapshot("AAPL", as_of=NOW) is None
    assert engine.execution_quote(_quote(), now=NOW + timedelta(milliseconds=500)) is None
    assert (
        engine.execution_quote(
            _quote(at=NOW + timedelta(seconds=1)), now=NOW + timedelta(seconds=1)
        )
        is not None
    )


def test_quote_snapshots_reject_out_of_order_future_and_wrong_scope() -> None:
    engine = OrderFlowEngineV13(OrderFlowPolicy(tracked_symbols=("AAPL",)))
    assert engine.execution_quote(_quote(), now=NOW) is not None
    assert engine.execution_quote(_quote(), now=NOW + timedelta(seconds=2)) is None
    assert engine.execution_quote(_quote(at=NOW + timedelta(seconds=5)), now=NOW) is None
    assert engine.execution_quote(_quote().model_copy(update={"symbol": "ASTS"}), now=NOW) is None
