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
    engine = PortfolioFlowEngine(
        PortfolioFlowPolicy(
            minimum_trades=3,
            minimum_volume=Decimal("30"),
            minimum_drop_percent=Decimal("0.3"),
            cooldown=timedelta(minutes=5),
        )
    )
    assert (
        engine.ingest(
            _event("quote", {"symbol": "TEST", "bid_price": "100.1", "ask_price": "100.2"}, NOW),
            now=NOW,
        )
        is None
    )
    alert = None
    for index, price in enumerate(("100", "99.7", "99.4")):
        alert = (
            engine.ingest(
                _event(
                    "trade",
                    {"symbol": "TEST", "price": price, "size": "10"},
                    NOW + timedelta(seconds=index),
                ),
                now=NOW + timedelta(seconds=index),
            )
            or alert
        )
    assert alert is not None
    assert alert.kind is AlertKind.PORTFOLIO_PROTECT
    assert alert.title == "PROTECT TEST"
    assert (
        engine.ingest(
            _event(
                "trade",
                {"symbol": "TEST", "price": "99", "size": "100"},
                NOW + timedelta(seconds=4),
            ),
            now=NOW + timedelta(seconds=4),
        )
        is None
    )


def test_emits_aggressive_entry_watch_for_concentrated_buying_at_the_ask() -> None:
    engine = PortfolioFlowEngine(
        PortfolioFlowPolicy(
            minimum_trades=3,
            minimum_volume=Decimal("30"),
            minimum_rise_percent=Decimal("0.3"),
            block_size=Decimal("10"),
        )
    )
    assert (
        engine.ingest(
            _event("quote", {"symbol": "TEST", "bid_price": "99.8", "ask_price": "99.9"}, NOW),
            now=NOW,
        )
        is None
    )

    alert = None
    for index, price in enumerate(("100", "100.3", "100.6")):
        alert = (
            engine.ingest(
                _event(
                    "trade",
                    {"symbol": "TEST", "price": price, "size": "10"},
                    NOW + timedelta(seconds=index),
                ),
                now=NOW + timedelta(seconds=index),
            )
            or alert
        )

    assert alert is not None
    assert alert.kind is AlertKind.PORTFOLIO_FLOW_BUY
    assert alert.title == "AGGRESSIVE ENTRY WATCH TEST"
    assert alert.component_analyses[0].engine_version == "2.0.0"
    assert alert.component_analyses[0].direction.value == "BULLISH"
    metrics = {item.name: item.value for item in alert.metrics}
    assert metrics["buy_volume_percent"] == Decimal("100")
    assert metrics["price_rise_percent"] >= Decimal("0.3")
    assert metrics["large_buy_blocks"] == 3


def test_inside_spread_trades_are_neutral_not_buy_pressure() -> None:
    engine = PortfolioFlowEngine(
        PortfolioFlowPolicy(
            minimum_trades=3,
            minimum_volume=Decimal("30"),
            minimum_rise_percent=Decimal("0.1"),
        )
    )
    engine.ingest(
        _event("quote", {"symbol": "TEST", "bid_price": "100", "ask_price": "101"}, NOW), now=NOW
    )

    alert = None
    for index, price in enumerate(("100.2", "100.4", "100.6")):
        alert = (
            engine.ingest(
                _event(
                    "trade",
                    {"symbol": "TEST", "price": price, "size": "10"},
                    NOW + timedelta(seconds=index),
                ),
                now=NOW + timedelta(seconds=index),
            )
            or alert
        )

    assert alert is None


def test_buy_and_sell_pressure_have_independent_cooldowns() -> None:
    engine = PortfolioFlowEngine(
        PortfolioFlowPolicy(
            minimum_trades=3,
            minimum_volume=Decimal("30"),
            minimum_drop_percent=Decimal("0.3"),
            minimum_rise_percent=Decimal("0.3"),
            cooldown=timedelta(minutes=5),
        )
    )
    engine.ingest(
        _event("quote", {"symbol": "TEST", "bid_price": "99.8", "ask_price": "99.9"}, NOW), now=NOW
    )
    buy_alert = None
    for index, price in enumerate(("100", "100.3", "100.6")):
        buy_alert = (
            engine.ingest(
                _event(
                    "trade",
                    {"symbol": "TEST", "price": price, "size": "10"},
                    NOW + timedelta(seconds=index),
                ),
                now=NOW + timedelta(seconds=index),
            )
            or buy_alert
        )

    reversal_at = NOW + timedelta(minutes=4)
    engine.ingest(
        _event("quote", {"symbol": "TEST", "bid_price": "101", "ask_price": "101.1"}, reversal_at),
        now=reversal_at,
    )
    sell_alert = None
    for index, price in enumerate(("101", "100.6", "100.2")):
        sell_alert = (
            engine.ingest(
                _event(
                    "trade",
                    {"symbol": "TEST", "price": price, "size": "10"},
                    reversal_at + timedelta(seconds=index),
                ),
                now=reversal_at + timedelta(seconds=index),
            )
            or sell_alert
        )

    assert buy_alert is not None and buy_alert.kind is AlertKind.PORTFOLIO_FLOW_BUY
    assert sell_alert is not None and sell_alert.kind is AlertKind.PORTFOLIO_PROTECT
