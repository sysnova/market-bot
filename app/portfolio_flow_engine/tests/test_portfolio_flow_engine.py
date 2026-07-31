from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.contracts import AlertKind, EventEnvelope
from app.portfolio_flow_engine import PortfolioFlowEngine, PortfolioFlowPolicy

NOW = datetime(2026, 7, 29, 18, tzinfo=UTC)


def _event(kind: str, payload: dict[str, object], at: datetime) -> EventEnvelope:
    return EventEnvelope(
        event_type=f"market.{kind}.received",
        occurred_at=at,
        source="alpaca_market_data",
        subject="TEST",
        payload=payload,
    )


def test_emits_one_protect_alert_for_concentrated_selling_then_cools_down() -> None:
    engine = PortfolioFlowEngine(PortfolioFlowPolicy(
        minimum_trades=3,
        minimum_volume=Decimal("30"),
        minimum_drop_percent=Decimal("0.3"),
        cooldown=timedelta(minutes=5),
    ))
    assert engine.ingest(_event("quote", {
        "symbol": "TEST", "bid_price": "100.1", "ask_price": "100.2"
    }, NOW), now=NOW) is None
    alert = None
    for index, price in enumerate(("100", "99.7", "99.4")):
        alert = engine.ingest(_event("trade", {
            "symbol": "TEST", "price": price, "size": "10"
        }, NOW + timedelta(seconds=index)), now=NOW + timedelta(seconds=index)) or alert
    assert alert is not None
    assert alert.kind is AlertKind.PORTFOLIO_PROTECT
    assert alert.title == "PROTECT TEST"
    assert engine.ingest(_event("trade", {
        "symbol": "TEST", "price": "99", "size": "100"
    }, NOW + timedelta(seconds=4)), now=NOW + timedelta(seconds=4)) is None
