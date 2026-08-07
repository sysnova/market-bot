from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from app.dilution_sec_engine import SecTickerNotFoundError, SecTransportError
from app.integration.peter_lynch_composition import PeterLynchRunService
from app.integration.peter_lynch_sec_adapter import SecPeterLynchFacts
from app.peter_lynch_engine import PeterLynchEngine


def facts(symbol: str) -> SecPeterLynchFacts:
    from app.peter_lynch_engine import AnnualEps

    return SecPeterLynchFacts(
        symbol=symbol,
        ttm_eps=Decimal("2"),
        prior_ttm_eps=Decimal("1.5"),
        annual_eps=(
            AnnualEps(2022, date(2022, 12, 31), Decimal("1")),
            AnnualEps(2023, date(2023, 12, 31), Decimal("1.25")),
            AnnualEps(2024, date(2024, 12, 31), Decimal("1.55")),
            AnnualEps(2025, date(2025, 12, 31), Decimal("1.9")),
        ),
        debt=Decimal("20"),
        equity=Decimal("100"),
        goodwill=Decimal("0"),
        intangibles_ex_goodwill=Decimal("0"),
        shares_outstanding=Decimal("300000000"),
        sic=7372,
        insider_open_market_purchase_count=1,
        latest_insider_purchase_at=date(2026, 6, 1),
        fundamentals_as_of=date(2026, 6, 30),
    )


@pytest.mark.unit
async def test_manual_run_evaluates_once_and_saves_only_non_transient_results() -> None:
    progress: list[str] = []
    store = AsyncMock()
    store.load_symbols.return_value = ("GOOD", "UNSUPPORTED", "BROKEN")
    prices = AsyncMock()
    prices.fetch_snapshots.return_value = {
        symbol: {"latestTrade": {"p": 20, "t": "2026-07-31T20:00:00Z"}}
        for symbol in ("GOOD", "UNSUPPORTED", "BROKEN")
    }
    resolver = AsyncMock()
    resolver.resolve.side_effect = [
        "0000000001",
        SecTickerNotFoundError("not found"),
        "0000000003",
    ]
    sec = AsyncMock()
    sec.load.side_effect = [facts("GOOD"), SecTransportError("offline")]
    store.save.return_value = 2

    summary = await PeterLynchRunService(
        store=store,
        prices=prices,
        ticker_resolver=resolver,
        sec=sec,
        calculator=PeterLynchEngine(),
        batch_size=100,
        progress=progress.append,
    ).run(now=datetime(2026, 8, 2, 12, tzinfo=UTC))

    assert summary == {
        "service": "peter-lynch-v1",
        "evaluated": 1,
        "selected": 1,
        "discarded": 0,
        "unsupported": 1,
        "errors": 1,
        "saved": 2,
    }
    saved = store.save.await_args.args[0]
    assert [item.symbol for item in saved] == ["GOOD", "UNSUPPORTED"]
    assert saved[0].eligible is True
    assert saved[1].eligible is False
    assert progress[0] == "Watchlist: 3 símbolos activos."
    assert any("[1/3] GOOD: consultando fundamentales SEC" in item for item in progress)
    assert any("GOOD: seleccionado 6/6" in item for item in progress)
    assert any("UNSUPPORTED: no soportado" in item for item in progress)
    assert any("BROKEN: error SEC" in item for item in progress)
    assert progress[-1] == "Persistencia: 2 evaluaciones actualizadas."


@pytest.mark.unit
async def test_alpaca_transport_failure_preserves_every_existing_tag() -> None:
    store = AsyncMock()
    store.load_symbols.return_value = ("A", "B")
    prices = AsyncMock()
    prices.fetch_snapshots.side_effect = RuntimeError("alpaca unavailable")

    summary = await PeterLynchRunService(
        store=store,
        prices=prices,
        ticker_resolver=AsyncMock(),
        sec=AsyncMock(),
        calculator=PeterLynchEngine(),
        batch_size=100,
    ).run(now=datetime(2026, 8, 2, 12, tzinfo=UTC))

    assert summary["errors"] == 2
    assert summary["saved"] == 0
    store.save.assert_not_awaited()


@pytest.mark.unit
async def test_stale_trade_uses_fresh_daily_bar_and_invalid_price_fails_closed() -> None:
    store = AsyncMock()
    store.load_symbols.return_value = ("FALLBACK", "STALE")
    prices = AsyncMock()
    prices.fetch_snapshots.return_value = {
        "FALLBACK": {
            "latestTrade": {"p": 30, "t": "2026-07-01T20:00:00Z"},
            "dailyBar": {"c": 20, "t": "2026-07-31T04:00:00Z"},
        },
        "STALE": {"latestTrade": {"p": 20, "t": "2026-07-01T20:00:00Z"}},
    }
    resolver = AsyncMock()
    resolver.resolve.side_effect = ["1", "2"]
    sec = AsyncMock()
    sec.load.side_effect = [facts("FALLBACK"), facts("STALE")]
    store.save.return_value = 2

    await PeterLynchRunService(
        store=store,
        prices=prices,
        ticker_resolver=resolver,
        sec=sec,
        calculator=PeterLynchEngine(),
        batch_size=100,
    ).run(now=datetime(2026, 8, 2, 12, tzinfo=UTC))

    saved = store.save.await_args.args[0]
    assert saved[0].metrics.trailing_pe == Decimal("10")
    assert saved[1].metrics.trailing_pe is None
