from __future__ import annotations

from datetime import date

import httpx
import pytest

from app.integration.peter_lynch_sec_adapter import PeterLynchSecAdapter


def _fact(
    value: int | float,
    *,
    end: str,
    filed: str,
    start: str | None = None,
    form: str = "10-K",
    frame: str | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "val": value,
        "end": end,
        "filed": filed,
        "form": form,
        "accn": f"0000000001-{filed[2:4]}-000001",
    }
    if start is not None:
        result["start"] = start
    if frame is not None:
        result["frame"] = frame
    return result


def _companyfacts() -> dict[str, object]:
    annual_eps = [
        _fact(1.0, start="2022-01-01", end="2022-12-31", filed="2023-02-01", frame="CY2022"),
        _fact(1.2, start="2023-01-01", end="2023-12-31", filed="2024-02-01", frame="CY2023"),
        _fact(1.5, start="2024-01-01", end="2024-12-31", filed="2025-02-01", frame="CY2024"),
        _fact(1.9, start="2025-01-01", end="2025-12-31", filed="2026-02-01", frame="CY2025"),
    ]
    interim_eps = [
        _fact(0.6, start="2024-01-01", end="2024-06-30", filed="2024-08-01", form="10-Q"),
        _fact(0.8, start="2025-01-01", end="2025-06-30", filed="2025-08-01", form="10-Q"),
        _fact(1.0, start="2026-01-01", end="2026-06-30", filed="2026-07-25", form="10-Q"),
    ]
    instant = {
        "LongTermDebtCurrent": 5_000_000,
        "LongTermDebtNoncurrent": 15_000_000,
        "StockholdersEquity": 100_000_000,
        "Goodwill": 4_000_000,
        "IntangibleAssetsNetExcludingGoodwill": 3_000_000,
        "CommonStockSharesOutstanding": 300_000_000,
    }
    us_gaap: dict[str, object] = {
        "EarningsPerShareDiluted": {"units": {"USD/shares": [*annual_eps, *interim_eps]}},
    }
    for concept, value in instant.items():
        unit = "shares" if concept == "CommonStockSharesOutstanding" else "USD"
        us_gaap[concept] = {
            "units": {
                unit: [_fact(value, end="2026-06-30", filed="2026-07-25", form="10-Q")]
            }
        }
    return {"facts": {"us-gaap": us_gaap}}


def _submissions() -> dict[str, object]:
    return {
        "sic": "7372",
        "filings": {
            "recent": {
                "accessionNumber": ["0000000001-26-000010", "0000000001-25-000010"],
                "filingDate": ["2026-06-10", "2025-01-10"],
                "form": ["4", "4"],
                "primaryDocument": ["form4.xml", "old-form4.xml"],
            },
            "files": [],
        },
    }


@pytest.mark.unit
async def test_maps_companyfacts_ttm_debt_equity_and_form4_purchase() -> None:
    requests: list[str] = []
    progress: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path.startswith("/submissions/"):
            return httpx.Response(200, json=_submissions())
        if request.url.path.startswith("/api/xbrl/companyfacts/"):
            return httpx.Response(200, json=_companyfacts())
        return httpx.Response(
            200,
            text="""
                <ownershipDocument><nonDerivativeTransaction>
                <transactionDate><value>2026-06-09</value></transactionDate>
                <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
                </nonDerivativeTransaction></ownershipDocument>
            """,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await PeterLynchSecAdapter(
            user_agent="MarketBot/0.1 operator@marketbot.test",
            client=client,
            progress=progress.append,
        ).load(cik="1", symbol="TEST", as_of=date(2026, 8, 2))

    assert result.symbol == "TEST"
    assert result.sic == 7372
    assert result.ttm_eps is not None and str(result.ttm_eps) == "2.1"
    assert result.prior_ttm_eps is not None and str(result.prior_ttm_eps) == "1.7"
    assert [item.fiscal_year for item in result.annual_eps] == [2022, 2023, 2024, 2025]
    assert result.debt == 20_000_000
    assert result.equity == 100_000_000
    assert result.shares_outstanding == 300_000_000
    assert result.insider_open_market_purchase_count == 1
    assert result.latest_insider_purchase_at == date(2026, 6, 9)
    assert requests[-1].endswith("/form4.xml")
    assert all("old-form4.xml" not in path for path in requests)
    assert progress == [
        "TEST: descargando submissions SEC.",
        "TEST: descargando CompanyFacts SEC.",
        "TEST: 1 Form 4 para revisar.",
        "TEST: revisando Form 4 1/1 (2026-06-10).",
        "TEST: compra insider encontrada (2026-06-09).",
        "TEST: fundamentales SEC normalizados.",
    ]


@pytest.mark.unit
async def test_missing_companyfacts_are_validly_normalized_as_unavailable() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/submissions/"):
            return httpx.Response(200, json={"sic": "", "filings": {"recent": {
                "accessionNumber": [], "filingDate": [], "form": [], "primaryDocument": []
            }, "files": []}})
        return httpx.Response(200, json={"facts": {}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await PeterLynchSecAdapter(
            user_agent="MarketBot/0.1 operator@marketbot.test", client=client
        ).load(cik="1", symbol="EMPTY", as_of=date(2026, 8, 2))

    assert result.ttm_eps is None
    assert result.annual_eps == ()
    assert result.insider_open_market_purchase_count == 0
