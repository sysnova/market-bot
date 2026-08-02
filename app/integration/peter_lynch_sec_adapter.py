"""Official SEC adapter for Peter Lynch fundamental screening inputs."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from time import monotonic
from typing import cast

import httpx

from app.dilution_sec_engine import (
    SecConfigurationError,
    SecEdgarAdapter,
    SecEdgarConfig,
    SecHttpStatusError,
    SecInvalidJsonError,
    SecPayloadError,
    SecRateLimitError,
    SecTimeoutError,
    SecTransportError,
)
from app.peter_lynch_engine import AnnualEps

_DATA_ORIGIN = "https://data.sec.gov"
_ARCHIVE_ORIGIN = "https://www.sec.gov"
_SAFE_DOCUMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$")
_NON_DERIVATIVE = re.compile(
    r"<nonDerivativeTransaction\b[^>]*>(.*?)</nonDerivativeTransaction\s*>",
    re.IGNORECASE | re.DOTALL,
)
_PURCHASE_CODE = re.compile(
    r"<transactionCode\b[^>]*>\s*P\s*</transactionCode\s*>", re.IGNORECASE
)
_TRANSACTION_DATE = re.compile(
    r"<transactionDate\b[^>]*>.*?<value\b[^>]*>\s*(\d{4}-\d{2}-\d{2})\s*</value\s*>",
    re.IGNORECASE | re.DOTALL,
)

_EPS_CONCEPTS = ("EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted")
_SHARE_CONCEPTS = (
    ("dei", "EntityCommonStockSharesOutstanding"),
    ("us-gaap", "CommonStockSharesOutstanding"),
)
_EQUITY_CONCEPTS = (
    "StockholdersEquity",
    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
)
_GOODWILL_CONCEPTS = ("Goodwill",)
_INTANGIBLE_CONCEPTS = (
    "IntangibleAssetsNetExcludingGoodwill",
    "FiniteLivedIntangibleAssetsNet",
)
_CURRENT_DEBT_CONCEPTS = (
    "LongTermDebtAndFinanceLeaseObligationsCurrent",
    "LongTermDebtCurrent",
    "CurrentPortionOfLongTermDebt",
)
_NONCURRENT_DEBT_CONCEPTS = (
    "LongTermDebtAndFinanceLeaseObligationsNoncurrent",
    "LongTermDebtNoncurrent",
)


@dataclass(frozen=True, slots=True)
class SecPeterLynchFacts:
    """SEC-only normalized facts later combined with an Alpaca price."""

    symbol: str
    ttm_eps: Decimal | None
    prior_ttm_eps: Decimal | None
    annual_eps: tuple[AnnualEps, ...]
    debt: Decimal | None
    equity: Decimal | None
    goodwill: Decimal | None
    intangibles_ex_goodwill: Decimal | None
    shares_outstanding: Decimal | None
    sic: int | None
    insider_open_market_purchase_count: int
    latest_insider_purchase_at: date | None
    fundamentals_as_of: date | None


@dataclass(frozen=True, slots=True)
class _Fact:
    value: Decimal
    end: date
    filed: date
    form: str
    accession: str
    start: date | None = None
    frame: str | None = None


@dataclass(frozen=True, slots=True)
class _Form4Reference:
    accession: str
    filed: date
    primary_document: str


class PeterLynchSecAdapter:
    """Read CompanyFacts, submissions, and bounded Form 4 XML from official SEC hosts."""

    def __init__(
        self,
        *,
        user_agent: str,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 15.0,
        minimum_request_interval_seconds: float = 0.11,
        max_form4_documents: int = 100,
    ) -> None:
        config = SecEdgarConfig(user_agent=user_agent, timeout_seconds=timeout_seconds)
        if minimum_request_interval_seconds < 0:
            raise SecConfigurationError("SEC request interval cannot be negative")
        if max_form4_documents < 1:
            raise SecConfigurationError("SEC max Form 4 documents must be positive")
        self._config = config
        self._client = client or httpx.AsyncClient()
        self._owns_client = client is None
        self._minimum_interval = minimum_request_interval_seconds
        self._max_form4_documents = max_form4_documents
        self._request_lock = asyncio.Lock()
        self._last_request_at: float | None = None

    async def __aenter__(self) -> PeterLynchSecAdapter:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def load(self, *, cik: str | int, symbol: str, as_of: date) -> SecPeterLynchFacts:
        normalized_cik = SecEdgarAdapter.normalize_cik(cik)
        submissions = _mapping(
            await self._request_json(f"/submissions/CIK{normalized_cik}.json"),
            "submissions",
        )
        companyfacts = _mapping(
            await self._request_json(
                f"/api/xbrl/companyfacts/CIK{normalized_cik}.json"
            ),
            "companyfacts",
        )
        filings = await self._form4_references(submissions, as_of=as_of)
        purchases: list[date] = []
        for reference in filings[: self._max_form4_documents]:
            document = await self._request_text(
                _archive_path(normalized_cik, reference)
            )
            purchases.extend(_open_market_purchase_dates(document, as_of=as_of))

        facts = _mapping(companyfacts.get("facts"), "companyfacts.facts")
        eps = _first_series(
            facts,
            (("us-gaap", item) for item in _EPS_CONCEPTS),
            "USD/shares",
            as_of,
        )
        annual_eps, annual_facts = _annual_eps(eps)
        ttm_eps, prior_ttm_eps = _ttm_pair(eps, annual_facts)
        current_debt = _latest_value(
            _first_series(
                facts,
                (("us-gaap", item) for item in _CURRENT_DEBT_CONCEPTS),
                "USD",
                as_of,
            )
        )
        noncurrent_debt = _latest_value(
            _first_series(
                facts,
                (("us-gaap", item) for item in _NONCURRENT_DEBT_CONCEPTS),
                "USD",
                as_of,
            )
        )
        debt = (
            current_debt.value + noncurrent_debt.value
            if current_debt is not None and noncurrent_debt is not None
            else None
        )
        equity = _latest_value(
            _first_series(facts, (("us-gaap", item) for item in _EQUITY_CONCEPTS), "USD", as_of)
        )
        goodwill = _latest_value(
            _first_series(facts, (("us-gaap", item) for item in _GOODWILL_CONCEPTS), "USD", as_of)
        )
        intangibles = _latest_value(
            _first_series(facts, (("us-gaap", item) for item in _INTANGIBLE_CONCEPTS), "USD", as_of)
        )
        shares = _latest_value(_first_series(facts, iter(_SHARE_CONCEPTS), "shares", as_of))
        selected = [
            item
            for item in (current_debt, noncurrent_debt, equity, goodwill, intangibles, shares)
            if item is not None
        ]
        selected.extend(annual_facts[-1:])
        return SecPeterLynchFacts(
            symbol=symbol.strip().upper(),
            ttm_eps=ttm_eps,
            prior_ttm_eps=prior_ttm_eps,
            annual_eps=annual_eps,
            debt=debt,
            equity=equity.value if equity is not None else None,
            goodwill=goodwill.value if goodwill is not None else None,
            intangibles_ex_goodwill=intangibles.value if intangibles is not None else None,
            shares_outstanding=shares.value if shares is not None else None,
            sic=_optional_sic(submissions.get("sic")),
            insider_open_market_purchase_count=len(purchases),
            latest_insider_purchase_at=max(purchases, default=None),
            fundamentals_as_of=max((item.end for item in selected), default=None),
        )

    async def _form4_references(
        self, submissions: Mapping[str, object], *, as_of: date
    ) -> tuple[_Form4Reference, ...]:
        cutoff = as_of - timedelta(days=365)
        filings = _mapping(submissions.get("filings"), "submissions.filings")
        payloads: list[Mapping[str, object]] = [
            _mapping(filings.get("recent"), "submissions.filings.recent")
        ]
        files = filings.get("files", [])
        if not isinstance(files, list):
            raise SecPayloadError("submissions.filings.files must be an array")
        for item in cast("list[object]", files):
            entry = _mapping(item, "submissions.filings.files entry")
            filing_to = _optional_date(entry.get("filingTo"))
            name = entry.get("name")
            if filing_to is not None and filing_to >= cutoff and isinstance(name, str):
                payloads.append(
                    _mapping(await self._request_json(f"/submissions/{name}"), name)
                )
        references: list[_Form4Reference] = []
        for payload in payloads:
            references.extend(_parse_form4_references(payload, cutoff=cutoff, as_of=as_of))
        return tuple(
            sorted(
                {item.accession: item for item in references}.values(),
                key=lambda item: (item.filed, item.accession),
                reverse=True,
            )
        )

    async def _request_json(self, endpoint: str) -> object:
        response = await self._request(f"{_DATA_ORIGIN}{endpoint}", endpoint)
        try:
            return response.json()
        except ValueError as error:
            raise SecInvalidJsonError(f"SEC returned invalid JSON at {endpoint}") from error

    async def _request_text(self, endpoint: str) -> str:
        response = await self._request(f"{_ARCHIVE_ORIGIN}{endpoint}", endpoint)
        if len(response.content) > 2_000_000:
            raise SecPayloadError("SEC Form 4 document exceeds two megabytes")
        return response.text

    async def _request(self, url: str, endpoint: str) -> httpx.Response:
        async with self._request_lock:
            if self._last_request_at is not None:
                remaining = self._minimum_interval - (monotonic() - self._last_request_at)
                if remaining > 0:
                    await asyncio.sleep(remaining)
            try:
                response = await self._client.get(
                    url,
                    headers={
                        "User-Agent": self._config.user_agent,
                        "Accept": "application/json, application/xml, text/xml",
                        "Accept-Encoding": "gzip, deflate",
                    },
                    timeout=self._config.timeout_seconds,
                )
            except httpx.TimeoutException as error:
                raise SecTimeoutError(f"SEC timeout at {endpoint}") from error
            except httpx.HTTPError as error:
                raise SecTransportError(f"SEC transport error at {endpoint}") from error
            finally:
                self._last_request_at = monotonic()
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            seconds = int(retry_after) if retry_after and retry_after.isdigit() else None
            raise SecRateLimitError(endpoint, seconds)
        if not 200 <= response.status_code < 300:
            raise SecHttpStatusError(endpoint, response.status_code)
        return response


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise SecPayloadError(f"SEC payload field {label} must be an object")
    untyped = cast("Mapping[object, object]", value)
    if any(not isinstance(key, str) for key in untyped):
        raise SecPayloadError(f"SEC payload field {label} has a non-string key")
    return cast("Mapping[str, object]", value)


def _first_series(
    facts: Mapping[str, object],
    concepts: Iterable[tuple[str, str]],
    unit: str,
    as_of: date,
) -> tuple[_Fact, ...]:
    for taxonomy, concept in concepts:
        taxonomy_value = facts.get(taxonomy)
        if not isinstance(taxonomy_value, Mapping):
            continue
        concept_value = cast("Mapping[object, object]", taxonomy_value).get(concept)
        if not isinstance(concept_value, Mapping):
            continue
        units_value = cast("Mapping[object, object]", concept_value).get("units")
        if not isinstance(units_value, Mapping):
            raise SecPayloadError(f"SEC CompanyFacts {concept}.units must be an object")
        records = cast("Mapping[object, object]", units_value).get(unit)
        if not isinstance(records, list):
            continue
        parsed = tuple(
            item
            for record in cast("list[object]", records)
            if (item := _parse_fact(record, concept, as_of)) is not None
        )
        if parsed:
            return parsed
    return ()


def _parse_fact(record: object, concept: str, as_of: date) -> _Fact | None:
    item = _mapping(record, f"{concept} entry")
    end = _required_date(item.get("end"), f"{concept}.end")
    filed = _required_date(item.get("filed"), f"{concept}.filed")
    if end > as_of or filed > as_of:
        return None
    accession = item.get("accn")
    form = item.get("form")
    if not isinstance(accession, str) or not accession or not isinstance(form, str):
        raise SecPayloadError(f"SEC CompanyFacts {concept} filing identity is invalid")
    start = _optional_date(item.get("start"))
    frame_value = item.get("frame")
    frame = frame_value if isinstance(frame_value, str) else None
    return _Fact(
        value=_decimal(item.get("val"), f"{concept}.val"),
        start=start,
        end=end,
        filed=filed,
        form=form.upper(),
        accession=accession,
        frame=frame,
    )


def _annual_eps(values: tuple[_Fact, ...]) -> tuple[tuple[AnnualEps, ...], tuple[_Fact, ...]]:
    latest_by_end: dict[date, _Fact] = {}
    for item in values:
        if item.form not in {"10-K", "10-K/A"} or item.start is None:
            continue
        if not 300 <= (item.end - item.start).days <= 430:
            continue
        existing = latest_by_end.get(item.end)
        if existing is None or item.filed > existing.filed:
            latest_by_end[item.end] = item
    facts = tuple(sorted(latest_by_end.values(), key=lambda item: item.end))
    annual = tuple(
        AnnualEps(_fiscal_year(item), item.end, item.value) for item in facts
    )
    return annual, facts


def _ttm_pair(
    all_eps: tuple[_Fact, ...], annual: tuple[_Fact, ...]
) -> tuple[Decimal | None, Decimal | None]:
    if not annual:
        return None, None
    latest_annual = annual[-1]
    previous_annual = annual[-2] if len(annual) >= 2 else None
    current_ytd = _latest_ytd_after(all_eps, latest_annual.end)
    if current_ytd is None:
        return latest_annual.value, previous_annual.value if previous_annual else None
    prior_ytd = _comparable_ytd(all_eps, current_ytd)
    if prior_ytd is None:
        return None, None
    ttm = latest_annual.value + current_ytd.value - prior_ytd.value
    if previous_annual is None:
        return ttm, None
    prior_prior_ytd = _comparable_ytd(all_eps, prior_ytd)
    prior_ttm = (
        previous_annual.value + prior_ytd.value - prior_prior_ytd.value
        if prior_prior_ytd is not None
        else previous_annual.value
    )
    return ttm, prior_ttm


def _latest_ytd_after(values: tuple[_Fact, ...], annual_end: date) -> _Fact | None:
    candidates = tuple(
        item
        for item in values
        if item.form in {"10-Q", "10-Q/A"}
        and item.start is not None
        and item.end > annual_end
        and 70 <= (item.end - item.start).days <= 300
    )
    return max(candidates, key=lambda item: (item.end, item.filed), default=None)


def _comparable_ytd(values: tuple[_Fact, ...], target: _Fact) -> _Fact | None:
    if target.start is None:
        return None
    duration = (target.end - target.start).days
    candidates = tuple(
        item
        for item in values
        if item.start is not None
        and item.end < target.end
        and 330 <= (target.end - item.end).days <= 400
        and abs((item.end - item.start).days - duration) <= 20
    )
    return min(
        candidates,
        key=lambda item: (abs((target.end - item.end).days - 365), -item.filed.toordinal()),
        default=None,
    )


def _latest_value(values: tuple[_Fact, ...]) -> _Fact | None:
    return max(values, key=lambda item: (item.end, item.filed), default=None)


def _fiscal_year(item: _Fact) -> int:
    if item.frame is not None:
        match = re.fullmatch(r"CY(\d{4})", item.frame)
        if match is not None:
            return int(match.group(1))
    return item.end.year


def _parse_form4_references(
    payload: Mapping[str, object], *, cutoff: date, as_of: date
) -> tuple[_Form4Reference, ...]:
    keys = ("accessionNumber", "filingDate", "form", "primaryDocument")
    columns = {key: _sequence(payload.get(key), key) for key in keys}
    sizes = {len(value) for value in columns.values()}
    if len(sizes) != 1:
        raise SecPayloadError("SEC Form 4 filing arrays have mismatched lengths")
    output: list[_Form4Reference] = []
    for index in range(next(iter(sizes), 0)):
        if str(columns["form"][index]).upper() not in {"4", "4/A"}:
            continue
        filed = _required_date(columns["filingDate"][index], "filingDate")
        if not cutoff <= filed <= as_of:
            continue
        accession = columns["accessionNumber"][index]
        document = columns["primaryDocument"][index]
        if not isinstance(accession, str) or not isinstance(document, str):
            raise SecPayloadError("SEC Form 4 filing identity is invalid")
        if (
            _SAFE_DOCUMENT.fullmatch(document) is None
            or any(part in {"", ".", ".."} for part in document.split("/"))
        ):
            raise SecPayloadError("SEC Form 4 primary document is unsafe")
        output.append(_Form4Reference(accession, filed, document))
    return tuple(output)


def _archive_path(cik: str, reference: _Form4Reference) -> str:
    accession = reference.accession.replace("-", "")
    return f"/Archives/edgar/data/{int(cik)}/{accession}/{reference.primary_document}"


def _open_market_purchase_dates(document: str, *, as_of: date) -> tuple[date, ...]:
    cutoff = as_of - timedelta(days=365)
    output: list[date] = []
    for block in _NON_DERIVATIVE.findall(document):
        if _PURCHASE_CODE.search(block) is None:
            continue
        match = _TRANSACTION_DATE.search(block)
        if match is None:
            continue
        try:
            transaction_date = date.fromisoformat(match.group(1))
        except ValueError as error:
            raise SecPayloadError("SEC Form 4 transaction date is invalid") from error
        if cutoff <= transaction_date <= as_of:
            output.append(transaction_date)
    return tuple(output)


def _sequence(value: object, label: str) -> Sequence[object]:
    if not isinstance(value, list):
        raise SecPayloadError(f"SEC payload field {label} must be an array")
    return cast("list[object]", value)


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


def _required_date(value: object, label: str) -> date:
    parsed = _optional_date(value)
    if parsed is None:
        raise SecPayloadError(f"SEC payload field {label} must be an ISO date")
    return parsed


def _optional_date(value: object) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _optional_sic(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None
