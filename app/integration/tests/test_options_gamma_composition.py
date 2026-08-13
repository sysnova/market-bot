from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.integration.options_gamma_composition import OptionsGammaRuntime
from app.options_gamma_engine import OptionContractSnapshot, OptionsGammaEngine

NOW = datetime(2026, 8, 12, 15, tzinfo=UTC)


class StockProvider:
    async def fetch_snapshots(self, symbols: tuple[str, ...]) -> dict[str, object]:
        symbol = symbols[0]
        if symbol == "BAD":
            raise RuntimeError("snapshot unavailable")
        return {
            symbol: {
                "latestTrade": {"p": 100, "t": "2026-08-12T15:00:00Z"},
            }
        }


class OptionProvider:
    async def fetch_chain(self, symbol: str, **_: object) -> tuple[OptionContractSnapshot, ...]:
        return (
            OptionContractSnapshot(
                symbol=f"{symbol}260814C00100000",
                underlying_symbol=symbol,
                expiration_date=date(2026, 8, 14),
                strike_price=Decimal("100"),
                option_type="call",
                open_interest=Decimal("1000"),
                open_interest_date=date(2026, 8, 11),
                gamma=Decimal("0.08"),
                implied_volatility=Decimal("0.40"),
                bid_price=Decimal("3"),
                ask_price=Decimal("3.2"),
                latest_trade_price=Decimal("3.1"),
                snapshot_at=NOW,
            ),
            OptionContractSnapshot(
                symbol=f"{symbol}260814P00100000",
                underlying_symbol=symbol,
                expiration_date=date(2026, 8, 14),
                strike_price=Decimal("100"),
                option_type="put",
                open_interest=Decimal("900"),
                open_interest_date=date(2026, 8, 11),
                gamma=Decimal("0.07"),
                implied_volatility=Decimal("0.40"),
                bid_price=Decimal("2.8"),
                ask_price=Decimal("3"),
                latest_trade_price=Decimal("2.9"),
                snapshot_at=NOW,
            ),
        )


class Publisher:
    def __init__(self) -> None:
        self.items: list[tuple[str, object]] = []

    async def publish(self, subject: str, envelope: object) -> None:
        self.items.append((subject, envelope))


@pytest.mark.unit
async def test_runtime_publishes_each_healthy_symbol_and_isolates_failures() -> None:
    publisher = Publisher()
    runtime = OptionsGammaRuntime(
        engine=OptionsGammaEngine(),
        stock_provider=StockProvider(),  # type: ignore[arg-type]
        option_provider=OptionProvider(),  # type: ignore[arg-type]
        publisher=publisher,  # type: ignore[arg-type]
        days_forward=45,
        strike_range_percent=Decimal("50"),
        concurrency=2,
    )
    runtime.set_symbols(("AAPL", "BAD"))

    summary = await runtime.refresh(now=NOW)

    assert summary.symbols_requested == 2
    assert summary.assessments_published == 1
    assert summary.failures == {"BAD": "RuntimeError"}
    assert [subject for subject, _ in publisher.items] == [
        "marketbot.v1.options-gamma.assessment.AAPL",
        "marketbot.v1.analysis.result.OPTIONS_GAMMA.AAPL",
    ]
