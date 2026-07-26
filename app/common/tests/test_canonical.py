from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel

from app.common.canonical import canonical_json, sha256_digest


class Side(StrEnum):
    BUY = "buy"


class Order(BaseModel):
    symbol: str
    quantity: Decimal


@dataclass(frozen=True)
class Envelope:
    order_id: UUID
    created_at: datetime
    side: Side
    order: Order


def test_canonical_json_sorts_keys_and_normalizes_domain_types() -> None:
    value = Envelope(
        order_id=UUID("018f05c9-8b40-7000-8000-000000000000"),
        created_at=datetime(2026, 7, 25, 18, 30, tzinfo=UTC),
        side=Side.BUY,
        order=Order(symbol="AAPL", quantity=Decimal("10.50")),
    )

    assert canonical_json(value) == (
        b'{"created_at":"2026-07-25T18:30:00Z","order":'
        b'{"quantity":"10.50","symbol":"AAPL"},"order_id":'
        b'"018f05c9-8b40-7000-8000-000000000000","side":"buy"}'
    )


def test_digest_is_hex_sha256_of_canonical_json() -> None:
    assert sha256_digest({"b": 2, "a": 1}) == (
        "43258cff783fe7036d8a43033f830adfc60ec037382473548ac742b888292777"
    )
