from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.contracts import ExecutionQuoteSnapshot, MarketQuote, ShortGate, ShortObservation

NOW = datetime(2026, 9, 25, 17, 7, tzinfo=UTC)


def test_quote_snapshot_keeps_causal_timestamps_and_roundtrips() -> None:
    quote = MarketQuote(
        symbol="ASTN",
        occurred_at=NOW,
        received_at=NOW + timedelta(milliseconds=20),
        bid_price=Decimal("17"),
        ask_price=Decimal("17.02"),
        bid_size=Decimal("10"),
        ask_size=Decimal("10"),
    )
    snapshot = ExecutionQuoteSnapshot(quote=quote, published_at=NOW + timedelta(milliseconds=30))
    assert (
        ExecutionQuoteSnapshot.model_validate_json(
            snapshot.model_dump_json(exclude_computed_fields=True)
        )
        == snapshot
    )
    with pytest.raises(ValueError):
        ExecutionQuoteSnapshot(quote=quote, published_at=NOW)


def test_observation_contract_cannot_be_enabled_or_ready_with_failed_gates() -> None:
    fields = dict(
        symbol="ASTS",
        instrument_symbol="ASTN",
        occurred_at=NOW,
        engine_version="1.4.0",
        route="SHORT_TACTICAL",
        status="BLOCKED",
        gates=(ShortGate(name="timing", passed=False, reason="timing_pending"),),
    )
    report = ShortObservation(**fields)
    assert report.orders_enabled is False
    with pytest.raises(ValueError):
        ShortObservation(**fields, orders_enabled=True)
    with pytest.raises(ValueError):
        ShortObservation(**(fields | {"status": "READY"}))
