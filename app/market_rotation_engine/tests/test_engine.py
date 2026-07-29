from decimal import Decimal

from app.market_rotation_engine import Bar, RotationEngine, SectorProfile


def test_rotation_engine_ranks_strength_and_returns_dynamic_candidates() -> None:
    rising = tuple(Bar(Decimal(100 + index), Decimal(1_000 + index * 20)) for index in range(60))
    flat = tuple(Bar(Decimal("100"), Decimal("1000")) for _ in range(60))
    profiles = (SectorProfile("TECH", "Technology", "XLK", "SPY", ("NVDA",)),)

    result = RotationEngine().analyze(
        profiles, {"XLK": rising, "SPY": flat, "NVDA": rising}
    )

    assert result[0]["code"] == "TECH"
    assert result[0]["state"] in {"INFLOW", "ACCUMULATING"}
    assert result[0]["evidence"][0]["symbol"] == "NVDA"
