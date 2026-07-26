"""Unit tests for the read-only SEC EDGAR adapter."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from app.dilution_sec_engine import DilutionSignal
from app.dilution_sec_engine.sec_adapter import (
    FilingDocumentReference,
    SecConfigurationError,
    SecDocumentSignalParser,
    SecEdgarAdapter,
    SecEdgarConfig,
    SecInvalidJsonError,
    SecPayloadError,
    SecRateLimitError,
    SecTimeoutError,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _json_fixture(name: str) -> object:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class StubSignalProvider:
    def __init__(self) -> None:
        self.references: list[FilingDocumentReference] = []

    async def signals_for(
        self, reference: FilingDocumentReference
    ) -> tuple[DilutionSignal, ...]:
        self.references.append(reference)
        if reference.form == "424B5":
            return (DilutionSignal.AT_THE_MARKET,)
        return ()


def _client(handler: httpx.MockTransport) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=handler)


@pytest.mark.asyncio
async def test_loads_official_cik_endpoints_and_maps_normalized_input() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.startswith("/submissions/"):
            return httpx.Response(200, json=_json_fixture("submissions.json"))
        return httpx.Response(200, json=_json_fixture("companyfacts.json"))

    provider = StubSignalProvider()
    async with _client(httpx.MockTransport(respond)) as client:
        adapter = SecEdgarAdapter(
            SecEdgarConfig(user_agent="MarketBot/0.1 operator@marketbot.test"),
            client=client,
            signal_provider=provider,
        )
        result = await adapter.load(cik="1234567", symbol="ACME", as_of=date(2026, 7, 26))

    assert [request.url.path for request in requests] == [
        "/submissions/CIK0001234567.json",
        "/api/xbrl/companyfacts/CIK0001234567.json",
    ]
    assert {request.headers["user-agent"] for request in requests} == {
        "MarketBot/0.1 operator@marketbot.test"
    }
    assert [filing.form for filing in result.filings] == ["424B5", "10-Q"]
    assert result.filings[0].signals == (DilutionSignal.AT_THE_MARKET,)
    assert result.facts is not None
    assert result.facts.current_shares_outstanding == 125_000_000
    assert result.facts.prior_year_shares_outstanding == 100_000_000
    assert result.facts.cash_and_equivalents == 15_000_000
    assert result.facts.quarterly_operating_cash_flow == -10_000_000
    assert len(provider.references) == 2
    assert provider.references[0].primary_document == "prospectus.htm"


@pytest.mark.asyncio
async def test_metadata_descriptions_never_create_textual_signals() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        name = "submissions.json" if "submissions" in request.url.path else "companyfacts.json"
        return httpx.Response(200, json=_json_fixture(name))

    async with _client(httpx.MockTransport(respond)) as client:
        result = await SecEdgarAdapter(
            SecEdgarConfig(user_agent="MarketBot/0.1 operator@marketbot.test"),
            client=client,
        ).load(cik=1234567, symbol="ACME", as_of=date(2026, 7, 26))

    assert all(filing.signals == () for filing in result.filings)


def test_configuration_requires_an_identifiable_user_agent_and_valid_cik() -> None:
    with pytest.raises(SecConfigurationError, match="contact email"):
        SecEdgarConfig(user_agent="MarketBot")
    with pytest.raises(SecConfigurationError, match="CIK"):
        SecEdgarAdapter.normalize_cik("ACME")


def test_safe_document_parser_is_bounded_and_ignores_active_html_content() -> None:
    parser = SecDocumentSignalParser(max_characters=300)

    signals = parser.parse(
        "<script>public offering</script><p>At-the-market offering and pre-funded warrants</p>"
    )

    assert signals == (DilutionSignal.AT_THE_MARKET, DilutionSignal.WARRANTS)
    with pytest.raises(SecPayloadError, match="character limit"):
        parser.parse("x" * 301)


def test_document_reference_accepts_sec_xsl_subdirectory_without_traversal() -> None:
    reference = FilingDocumentReference(
        cik="0000320193",
        accession_number="0000320193-26-000001",
        form="4",
        filed_at=date(2026, 7, 24),
        primary_document="xslF345X06/form4.xml",
    )

    assert reference.primary_document == "xslF345X06/form4.xml"
    with pytest.raises(ValueError, match="safe SEC file"):
        FilingDocumentReference(
            **{**reference.model_dump(), "primary_document": "../form4.xml"}
        )


@pytest.mark.asyncio
async def test_rate_limit_has_typed_error_and_retry_after() -> None:
    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "12"})

    async with _client(httpx.MockTransport(respond)) as client:
        adapter = SecEdgarAdapter(
            SecEdgarConfig(user_agent="MarketBot/0.1 operator@marketbot.test"),
            client=client,
        )
        with pytest.raises(SecRateLimitError) as caught:
            await adapter.load(cik=1234567, symbol="ACME", as_of=date(2026, 7, 26))

    assert caught.value.retry_after_seconds == 12


@pytest.mark.asyncio
async def test_timeout_is_translated_without_retrying_or_hiding_endpoint() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    async with _client(httpx.MockTransport(timeout)) as client:
        adapter = SecEdgarAdapter(
            SecEdgarConfig(user_agent="MarketBot/0.1 operator@marketbot.test"),
            client=client,
        )
        with pytest.raises(SecTimeoutError, match="submissions"):
            await adapter.load(cik=1234567, symbol="ACME", as_of=date(2026, 7, 26))


@pytest.mark.asyncio
async def test_invalid_json_and_malformed_sec_shape_have_distinct_errors() -> None:
    def invalid_json(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not-json")

    async with _client(httpx.MockTransport(invalid_json)) as client:
        adapter = SecEdgarAdapter(
            SecEdgarConfig(user_agent="MarketBot/0.1 operator@marketbot.test"),
            client=client,
        )
        with pytest.raises(SecInvalidJsonError):
            await adapter.load(cik=1234567, symbol="ACME", as_of=date(2026, 7, 26))

    def malformed(request: httpx.Request) -> httpx.Response:
        if "submissions" in request.url.path:
            return httpx.Response(200, json={"filings": {"recent": {"form": []}}})
        return httpx.Response(200, json=_json_fixture("companyfacts.json"))

    async with _client(httpx.MockTransport(malformed)) as client:
        adapter = SecEdgarAdapter(
            SecEdgarConfig(user_agent="MarketBot/0.1 operator@marketbot.test"),
            client=client,
        )
        with pytest.raises(SecPayloadError, match="accessionNumber"):
            await adapter.load(cik=1234567, symbol="ACME", as_of=date(2026, 7, 26))
