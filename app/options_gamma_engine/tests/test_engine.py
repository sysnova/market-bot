from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.options_gamma_engine import (
    OptionContractSnapshot,
    OptionsGammaContext,
    OptionsGammaEngine,
)

NOW = datetime(2026, 8, 12, 15, tzinfo=UTC)
EXPIRATION = date(2026, 8, 14)


def contract(
    symbol: str,
    strike: str,
    option_type: str,
    open_interest: str,
    gamma: str | None,
    *,
    bid: str = "2.90",
    ask: str = "3.10",
) -> OptionContractSnapshot:
    return OptionContractSnapshot(
        symbol=symbol,
        underlying_symbol="AAPL",
        expiration_date=EXPIRATION,
        strike_price=Decimal(strike),
        option_type=option_type,
        open_interest=Decimal(open_interest),
        open_interest_date=date(2026, 8, 11),
        gamma=Decimal(gamma) if gamma is not None else None,
        implied_volatility=Decimal("0.40"),
        bid_price=Decimal(bid),
        ask_price=Decimal(ask),
        latest_trade_price=Decimal("3"),
        snapshot_at=NOW,
    )


def context(contracts: tuple[OptionContractSnapshot, ...]) -> OptionsGammaContext:
    return OptionsGammaContext(
        symbol="AAPL",
        spot_price=Decimal("100"),
        spot_as_of=NOW,
        generated_at=NOW,
        expiration_from=date(2026, 8, 12),
        expiration_to=date(2026, 9, 25),
        contracts=contracts,
    )


@pytest.mark.unit
def test_provider_warnings_are_preserved_in_assessment() -> None:
    base = context(())
    result = OptionsGammaEngine().evaluate(
        base.__class__(
            symbol=base.symbol,
            spot_price=base.spot_price,
            spot_as_of=base.spot_as_of,
            generated_at=base.generated_at,
            expiration_from=base.expiration_from,
            expiration_to=base.expiration_to,
            contracts=base.contracts,
            provider_warnings=("open_interest_source_unavailable",),
        )
    )

    assert "open_interest_source_unavailable" in result.warnings


@pytest.mark.unit
def test_engine_finds_walls_max_pain_and_expected_move() -> None:
    result = OptionsGammaEngine().evaluate(
        context(
            (
                contract("AAPL260814C00095000", "95", "call", "100", "0.02"),
                contract("AAPL260814P00095000", "95", "put", "2000", "0.04"),
                contract("AAPL260814C00100000", "100", "call", "1200", "0.08"),
                contract("AAPL260814P00100000", "100", "put", "900", "0.07"),
                contract("AAPL260814C00105000", "105", "call", "200", "0.03"),
                contract("AAPL260814P00105000", "105", "put", "100", "0.03"),
            )
        )
    )

    assert result.status == "AVAILABLE"
    assert result.call_wall == Decimal("100.0000")
    assert result.put_wall == Decimal("95.0000")
    assert result.absolute_gamma_wall == Decimal("100.0000")
    assert result.max_pain == Decimal("100.0000")
    assert result.expected_move_low == Decimal("94.0000")
    assert result.expected_move_high == Decimal("106.0000")
    assert result.coverage_ratio == Decimal("1.0000")
    assert result.quality_score >= Decimal("70")
    assert result.expires_at > NOW
    assert len(result.expirations) == 1


@pytest.mark.unit
def test_engine_marks_chain_unavailable_without_usable_gamma_and_oi() -> None:
    result = OptionsGammaEngine().evaluate(
        context(
            (
                contract(
                    "AAPL260814C00100000",
                    "100",
                    "call",
                    "0",
                    None,
                ),
            )
        )
    )

    assert result.status == "UNAVAILABLE"
    assert result.usable_contract_count == 0
    assert result.quality_score == Decimal("0.0000")
    assert result.call_wall is None
    assert "no_usable_contracts" in result.warnings
