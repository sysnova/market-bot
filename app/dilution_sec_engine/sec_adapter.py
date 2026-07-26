"""Read-only adapter for official SEC submissions and CompanyFacts endpoints."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from html import unescape
from math import isfinite
from typing import Annotated, Protocol, cast

import httpx
from pydantic import StringConstraints, ValidationError, field_validator

from app.contracts import StrictFrozenModel

from .models import (
    AccessionNumber,
    CompanyFactsSnapshot,
    DilutionEvaluationInput,
    DilutionSignal,
    NonEmptyText,
    SecFiling,
)

_SEC_ORIGIN = "https://data.sec.gov"
_EMAIL_PATTERN = re.compile(r"(?<![\w.+-])[\w.+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![\w.-])")
_SAFE_DOCUMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$")
_SCRIPT_OR_STYLE = re.compile(r"<(script|style)\b[^>]*>.*?</\1\s*>", re.DOTALL | re.IGNORECASE)
_HTML_TAG = re.compile(r"<[^>]{0,1000}>")
_WHITESPACE = re.compile(r"\s+")
_SHARE_CONCEPTS = (
    ("dei", "EntityCommonStockSharesOutstanding"),
    ("us-gaap", "CommonStockSharesOutstanding"),
)
_CASH_CONCEPTS = (
    ("us-gaap", "CashAndCashEquivalentsAtCarryingValue"),
    ("us-gaap", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"),
)
_OPERATING_CASH_FLOW_CONCEPTS = (
    ("us-gaap", "NetCashProvidedByUsedInOperatingActivities"),
)
_DOCUMENT_SIGNAL_PATTERNS: tuple[tuple[DilutionSignal, re.Pattern[str]], ...] = (
    (
        DilutionSignal.AT_THE_MARKET,
        re.compile(r"\bat[-\s]the[-\s]market\s+(?:offering|program|agreement)\b"),
    ),
    (DilutionSignal.SHELF_REGISTRATION, re.compile(r"\bshelf registration\b")),
    (DilutionSignal.PUBLIC_OFFERING, re.compile(r"\bpublic offering\b")),
    (DilutionSignal.REGISTERED_DIRECT, re.compile(r"\bregistered direct\b")),
    (DilutionSignal.PRIVATE_PLACEMENT, re.compile(r"\bprivate placement\b")),
    (
        DilutionSignal.SELLING_STOCKHOLDERS,
        re.compile(r"\bselling (?:stockholders?|shareholders?)\b"),
    ),
    (
        DilutionSignal.WARRANTS,
        re.compile(r"\b(?:pre-funded\s+)?warrants?\b"),
    ),
    (
        DilutionSignal.CONVERTIBLE_DEBT,
        re.compile(r"\bconvertible (?:senior )?notes?\b|\bconversion price\b"),
    ),
    (DilutionSignal.GOING_CONCERN, re.compile(r"\bgoing concern\b")),
    (
        DilutionSignal.REVERSE_SPLIT,
        re.compile(r"\breverse (?:stock )?split\b"),
    ),
)
Cik = Annotated[str, StringConstraints(pattern=r"^\d{10}$")]


class SecAdapterError(RuntimeError):
    """Base error for failures at the external SEC boundary."""


class SecConfigurationError(SecAdapterError):
    """Local configuration is unsafe or insufficient for SEC access."""


class SecRateLimitError(SecAdapterError):
    """SEC rejected the request because the caller is rate limited."""

    def __init__(self, endpoint: str, retry_after_seconds: int | None) -> None:
        self.endpoint = endpoint
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"SEC rate limit at {endpoint}")


class SecTimeoutError(SecAdapterError):
    """The configured SEC request deadline expired."""


class SecTransportError(SecAdapterError):
    """The SEC request failed before an HTTP response was available."""


class SecHttpStatusError(SecAdapterError):
    """SEC returned an unexpected non-success HTTP status."""

    def __init__(self, endpoint: str, status_code: int) -> None:
        self.endpoint = endpoint
        self.status_code = status_code
        super().__init__(f"SEC returned HTTP {status_code} at {endpoint}")


class SecInvalidJsonError(SecAdapterError):
    """SEC returned a body that was not valid JSON."""


class SecPayloadError(SecAdapterError):
    """SEC JSON did not satisfy the documented shape needed by this adapter."""


@dataclass(frozen=True, slots=True)
class SecEdgarConfig:
    """Operator-provided SEC identity and transport deadline."""

    user_agent: str
    timeout_seconds: float = 10.0
    max_recent_filings: int = 200
    max_signal_documents: int = 5

    def __post_init__(self) -> None:
        user_agent = self.user_agent.strip()
        if not user_agent or _EMAIL_PATTERN.search(user_agent) is None:
            raise SecConfigurationError(
                "SEC User-Agent must include an identifiable contact email"
            )
        if not isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise SecConfigurationError("SEC timeout_seconds must be positive")
        if isinstance(self.max_recent_filings, bool) or self.max_recent_filings <= 0:
            raise SecConfigurationError("SEC max_recent_filings must be positive")
        if isinstance(self.max_signal_documents, bool) or self.max_signal_documents < 0:
            raise SecConfigurationError("SEC max_signal_documents must not be negative")
        object.__setattr__(self, "user_agent", user_agent)


class FilingDocumentReference(StrictFrozenModel):
    """Safe locator passed to an explicitly injected primary-document provider."""

    cik: Cik
    accession_number: AccessionNumber
    form: NonEmptyText
    filed_at: date
    primary_document: NonEmptyText
    primary_document_description: str = ""

    @field_validator("primary_document", mode="after")
    @classmethod
    def validate_safe_document_name(cls, value: str) -> str:
        segments = value.split("/")
        if (
            _SAFE_DOCUMENT.fullmatch(value) is None
            or any(segment in {"", ".", ".."} for segment in segments)
        ):
            raise ValueError("primary document must be a safe SEC file name")
        return value


class FilingSignalProvider(Protocol):
    """Port for document-backed signal extraction; metadata is never parsed as text."""

    async def signals_for(
        self, reference: FilingDocumentReference
    ) -> tuple[DilutionSignal, ...]: ...


class FilingDocumentLoader(Protocol):
    """I/O port that may retrieve one bounded primary SEC document as text."""

    async def load_text(self, reference: FilingDocumentReference) -> str: ...


@dataclass(frozen=True, slots=True)
class SecDocumentSignalParser:
    """Bounded, non-executing extraction of explicit phrases from primary filings."""

    max_characters: int = 1_000_000

    def __post_init__(self) -> None:
        if isinstance(self.max_characters, bool) or self.max_characters <= 0:
            raise SecConfigurationError("SEC parser max_characters must be positive")

    def parse(self, document_text: str) -> tuple[DilutionSignal, ...]:
        if len(document_text) > self.max_characters:
            raise SecPayloadError("SEC primary document exceeds parser character limit")
        without_active_content = _SCRIPT_OR_STYLE.sub(" ", document_text)
        plain_text = _WHITESPACE.sub(
            " ", _HTML_TAG.sub(" ", unescape(without_active_content))
        ).lower()
        return tuple(
            signal
            for signal, pattern in _DOCUMENT_SIGNAL_PATTERNS
            if pattern.search(plain_text) is not None
        )


@dataclass(frozen=True, slots=True)
class ParsedFilingSignalProvider:
    """Compose an injected document loader with the safe pure-text parser."""

    loader: FilingDocumentLoader
    parser: SecDocumentSignalParser

    async def signals_for(
        self, reference: FilingDocumentReference
    ) -> tuple[DilutionSignal, ...]:
        return self.parser.parse(await self.loader.load_text(reference))


class SecEdgarAdapter:
    """Fetch two official CIK resources and normalize them for the pure engine."""

    def __init__(
        self,
        config: SecEdgarConfig,
        *,
        client: httpx.AsyncClient | None = None,
        signal_provider: FilingSignalProvider | None = None,
    ) -> None:
        self._config = config
        self._client = client or httpx.AsyncClient()
        self._owns_client = client is None
        self._signal_provider = signal_provider

    async def __aenter__(self) -> SecEdgarAdapter:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close only the HTTP client created by this adapter."""

        if self._owns_client:
            await self._client.aclose()

    @staticmethod
    def normalize_cik(cik: str | int) -> str:
        """Normalize a caller-supplied CIK without performing ticker discovery."""

        if isinstance(cik, bool):
            raise SecConfigurationError("CIK must contain one to ten digits")
        raw = str(cik).strip()
        if not raw.isdigit() or not (1 <= len(raw) <= 10) or int(raw) <= 0:
            raise SecConfigurationError("CIK must contain one to ten digits")
        return raw.zfill(10)

    async def load(
        self,
        *,
        cik: str | int,
        symbol: str,
        as_of: date,
    ) -> DilutionEvaluationInput:
        """Load SEC data by CIK and build the deterministic engine input."""

        normalized_cik = self.normalize_cik(cik)
        submissions_endpoint = f"/submissions/CIK{normalized_cik}.json"
        companyfacts_endpoint = (
            f"/api/xbrl/companyfacts/CIK{normalized_cik}.json"
        )
        submissions = await self._request_json(submissions_endpoint)
        companyfacts = await self._request_json(companyfacts_endpoint)
        filings = await self._map_filings(normalized_cik, submissions)
        facts = self._map_companyfacts(companyfacts, as_of)
        try:
            return DilutionEvaluationInput(
                symbol=symbol,
                as_of=as_of,
                filings=filings,
                facts=facts,
            )
        except ValidationError as error:
            raise SecPayloadError(f"normalized SEC input is invalid: {error}") from error

    async def _request_json(self, endpoint: str) -> object:
        url = f"{_SEC_ORIGIN}{endpoint}"
        headers = {
            "User-Agent": self._config.user_agent,
            "Accept": "application/json",
            "Accept-Encoding": "gzip, deflate",
        }
        try:
            response = await self._client.get(
                url,
                headers=headers,
                timeout=self._config.timeout_seconds,
            )
        except httpx.TimeoutException as error:
            raise SecTimeoutError(f"SEC timeout at {endpoint}") from error
        except httpx.HTTPError as error:
            raise SecTransportError(f"SEC transport error at {endpoint}") from error
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            retry_seconds = int(retry_after) if retry_after and retry_after.isdigit() else None
            raise SecRateLimitError(endpoint, retry_seconds)
        if not 200 <= response.status_code < 300:
            raise SecHttpStatusError(endpoint, response.status_code)
        try:
            payload: object = response.json()
        except ValueError as error:
            raise SecInvalidJsonError(f"SEC returned invalid JSON at {endpoint}") from error
        return payload

    async def _map_filings(
        self, cik: str, payload: object
    ) -> tuple[SecFiling, ...]:
        root = _mapping(payload, "submissions")
        filings = _mapping(root.get("filings"), "filings")
        recent = _mapping(filings.get("recent"), "filings.recent")
        keys = (
            "accessionNumber",
            "filingDate",
            "form",
            "primaryDocument",
            "primaryDocDescription",
        )
        columns = {key: _list(recent.get(key), key) for key in keys}
        sizes = {len(column) for column in columns.values()}
        if len(sizes) != 1:
            raise SecPayloadError("SEC filings.recent arrays have mismatched lengths")
        mapped: list[SecFiling] = []
        signal_documents = 0
        available_count = next(iter(sizes), 0)
        for index in range(min(available_count, self._config.max_recent_filings)):
            accession = _text(columns["accessionNumber"][index], "accessionNumber")
            form = _text(columns["form"][index], "form")
            filed_at = _date(columns["filingDate"][index], "filingDate")
            primary_document = _text(
                columns["primaryDocument"][index], "primaryDocument"
            )
            description_value = columns["primaryDocDescription"][index]
            description = description_value if isinstance(description_value, str) else ""
            try:
                reference = FilingDocumentReference(
                    cik=cik,
                    accession_number=accession,
                    form=form,
                    filed_at=filed_at,
                    primary_document=primary_document,
                    primary_document_description=description,
                )
                signals: tuple[DilutionSignal, ...] = ()
                if (
                    self._signal_provider is not None
                    and signal_documents < self._config.max_signal_documents
                ):
                    signals = await self._signal_provider.signals_for(reference)
                    signal_documents += 1
                mapped.append(
                    SecFiling(
                        accession_number=accession,
                        form=form,
                        filed_at=filed_at,
                        primary_document_description=description,
                        signals=signals,
                    )
                )
            except ValidationError as error:
                raise SecPayloadError(f"invalid SEC filing at index {index}: {error}") from error
        return tuple(mapped)

    @staticmethod
    def _map_companyfacts(
        payload: object, as_of: date
    ) -> CompanyFactsSnapshot | None:
        root = _mapping(payload, "companyfacts")
        facts = _mapping(root.get("facts"), "facts")
        shares = _first_series(facts, _SHARE_CONCEPTS, "shares", as_of)
        current_share, prior_share = _share_pair(shares)
        cash = _latest(_first_series(facts, _CASH_CONCEPTS, "USD", as_of))
        cash_flow = _latest_quarter(
            _first_series(facts, _OPERATING_CASH_FLOW_CONCEPTS, "USD", as_of)
        )
        if cash is not None and cash_flow is not None:
            end_gap = abs((cash.end - cash_flow.end).days)
            if end_gap > 10:
                cash = None
                cash_flow = None
        else:
            cash = None
            cash_flow = None
        if current_share is None or prior_share is None:
            current_share = None
            prior_share = None
        if current_share is None and cash is None:
            return None
        selected = tuple(
            item
            for item in (current_share, prior_share, cash, cash_flow)
            if item is not None
        )
        try:
            return CompanyFactsSnapshot(
                period_end=max(item.end for item in selected),
                current_shares_outstanding=(
                    current_share.value if current_share is not None else None
                ),
                prior_year_shares_outstanding=(
                    prior_share.value if prior_share is not None else None
                ),
                cash_and_equivalents=cash.value if cash is not None else None,
                quarterly_operating_cash_flow=(
                    cash_flow.value if cash_flow is not None else None
                ),
                source_accessions=tuple(item.accession for item in selected),
            )
        except ValidationError as error:
            raise SecPayloadError(f"invalid SEC CompanyFacts values: {error}") from error


@dataclass(frozen=True, slots=True)
class _FactValue:
    end: date
    value: Decimal
    accession: str
    filed_at: date
    start: date | None = None


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise SecPayloadError(f"SEC payload field {label} must be an object")
    untyped = cast("Mapping[object, object]", value)
    if any(not isinstance(key, str) for key in untyped):
        raise SecPayloadError(f"SEC payload field {label} has a non-string key")
    return cast("Mapping[str, object]", value)


def _list(value: object, label: str) -> Sequence[object]:
    if not isinstance(value, list):
        raise SecPayloadError(f"SEC payload field {label} must be an array")
    return cast("list[object]", value)


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SecPayloadError(f"SEC payload field {label} must be non-empty text")
    return value.strip()


def _date(value: object, label: str) -> date:
    text = _text(value, label)
    try:
        return date.fromisoformat(text)
    except ValueError as error:
        raise SecPayloadError(f"SEC payload field {label} has an invalid date") from error


def _decimal(value: object, label: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise SecPayloadError(f"SEC payload field {label} must be numeric")
    try:
        parsed = Decimal(str(value))
    except InvalidOperation as error:
        raise SecPayloadError(f"SEC payload field {label} must be numeric") from error
    if not parsed.is_finite():
        raise SecPayloadError(f"SEC payload field {label} must be finite")
    return parsed


def _first_series(
    facts: Mapping[str, object],
    concepts: tuple[tuple[str, str], ...],
    unit: str,
    as_of: date,
) -> tuple[_FactValue, ...]:
    for taxonomy, concept in concepts:
        taxonomy_value = facts.get(taxonomy)
        if not isinstance(taxonomy_value, Mapping):
            continue
        taxonomy_map = _mapping(cast("object", taxonomy_value), taxonomy)
        concept_value = taxonomy_map.get(concept)
        if not isinstance(concept_value, Mapping):
            continue
        concept_map = _mapping(cast("object", concept_value), concept)
        units_value = concept_map.get("units")
        if not isinstance(units_value, Mapping):
            raise SecPayloadError(f"SEC CompanyFacts {concept}.units must be an object")
        units = _mapping(cast("object", units_value), f"{concept}.units")
        records_value = units.get(unit)
        if not isinstance(records_value, list):
            continue
        records = cast("list[object]", records_value)
        parsed = tuple(
            item
            for record in records
            if (item := _fact_value(record, concept, as_of)) is not None
        )
        if parsed:
            return parsed
    return ()


def _fact_value(record: object, concept: str, as_of: date) -> _FactValue | None:
    if not isinstance(record, Mapping):
        raise SecPayloadError(f"SEC CompanyFacts {concept} entry must be an object")
    record_map = _mapping(cast("object", record), f"{concept} entry")
    end = _date(record_map.get("end"), f"{concept}.end")
    filed_at = _date(record_map.get("filed"), f"{concept}.filed")
    if end > as_of or filed_at > as_of:
        return None
    accession = _text(record_map.get("accn"), f"{concept}.accn")
    start_value = record_map.get("start")
    start = _date(start_value, f"{concept}.start") if start_value is not None else None
    return _FactValue(
        end=end,
        value=_decimal(record_map.get("val"), f"{concept}.val"),
        accession=accession,
        filed_at=filed_at,
        start=start,
    )


def _latest(values: tuple[_FactValue, ...]) -> _FactValue | None:
    return max(values, key=lambda item: (item.end, item.filed_at), default=None)


def _latest_quarter(values: tuple[_FactValue, ...]) -> _FactValue | None:
    quarterly = tuple(
        item
        for item in values
        if item.start is not None and 70 <= (item.end - item.start).days <= 110
    )
    return _latest(quarterly)


def _share_pair(
    values: tuple[_FactValue, ...],
) -> tuple[_FactValue | None, _FactValue | None]:
    latest_by_end: dict[date, _FactValue] = {}
    for item in values:
        existing = latest_by_end.get(item.end)
        if existing is None or item.filed_at > existing.filed_at:
            latest_by_end[item.end] = item
    current = _latest(tuple(latest_by_end.values()))
    if current is None:
        return None, None
    candidates = tuple(
        item
        for item in latest_by_end.values()
        if 300 <= (current.end - item.end).days <= 430
    )
    if not candidates:
        return None, None
    prior = min(
        candidates,
        key=lambda item: (abs((current.end - item.end).days - 365), -item.end.toordinal()),
    )
    return current, prior
