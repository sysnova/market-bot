# Dilution SEC Engine

Deterministic, network-free assessment of equity dilution risk from normalized SEC inputs. It is an analytical warning system only; it cannot create or execute orders.

## Boundary

The engine accepts:

- SEC filing metadata, keyed by accession number;
- normalized signals extracted upstream from primary filings;
- an optional SEC CompanyFacts snapshot for year-over-year shares and cash-burn runway;
- an explicit evaluation date.

It does **not** call SEC EDGAR, parse HTML, read secrets, use a clock, query a database, or import another engine. An adapter may obtain SEC documents and map phrases such as ATM, public offering, warrants, convertibles, going concern, and reverse split to `DilutionSignal` values before calling the core.

## Public output

`DilutionSecEngine.evaluate()` emits the shared `AnalysisResult` contract with:

- `horizon=DILUTION`;
- a 0–100 risk score;
- `FAVORABLE`, `WATCH`, `CAUTION`, `AVOID`, or `INSUFFICIENT_DATA`;
- reasons and structured evidence in metrics;
- a stable context hash and deterministic UUIDv7 analysis identity.

`DilutionSecEngine.assess()` exposes the engine-owned `DilutionAssessment` when a consumer needs typed severity and evidence directly.

## Version 1 policy

Only the strongest qualifying offering/registration filing contributes a form score, and each normalized signal contributes at most once. This prevents multiple documents from the same financing transaction from multiplying risk.

Current thresholds are:

| Score | Severity |
| ---: | --- |
| no input | unknown |
| 0–14 | low |
| 15–34 | medium |
| 35–69 | high |
| 70–100 | critical |

Filings older than 365 days are excluded. A recent executed-offering form such as `424B5` contributes more than a registration form such as `S-3`. Large growth in shares outstanding and less than four quarters of estimated operating cash runway add financing-pressure evidence. The score is an alerting heuristic, not a prediction that issuance will occur.

## Example

```python
from app.dilution_sec_engine import DilutionSecEngine, DilutionEvaluationInput

request = DilutionEvaluationInput.model_validate_json(sec_snapshot_json)
analysis = DilutionSecEngine().evaluate(request)
```

The fixtures under `tests/fixtures/` are synthetic and contain no credentials or live network dependency.

## SEC EDGAR adapter

`SecEdgarAdapter` is the read-only production boundary for these official resources:

- `https://data.sec.gov/submissions/CIK##########.json`
- `https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json`

`SecTickerResolver` maps symbols through the official
`https://www.sec.gov/files/company_tickers.json` file and caches that map once per process. The
adapter must not be called on every market tick; cache or schedule snapshots by CIK and refresh them
on an operational cadence appropriate for filings.

SEC requires an identifiable `User-Agent`. Pass an application name plus a monitored contact email from runtime configuration; never hard-code a personal address in Git:

```python
from app.dilution_sec_engine import SecEdgarAdapter, SecEdgarConfig

config = SecEdgarConfig(
    user_agent=settings.sec_user_agent,
    timeout_seconds=10,
)
async with SecEdgarAdapter(config) as sec:
    engine_input = await sec.load(cik="0000320193", symbol="AAPL", as_of=run_date)
```

An injected `httpx.AsyncClient` is never closed by the adapter. A client created internally is closed by its async context manager. HTTP 429, timeouts, invalid JSON, malformed payloads, transport failures, and unexpected status codes are exposed as typed `SecAdapterError` subclasses. The adapter does not automatically retry; supervisors can honor `SecRateLimitError.retry_after_seconds` and apply bounded backoff.

### Document evidence

Submission descriptions never create dilution signals. To enrich primary filings, inject a `FilingSignalProvider`. `ParsedFilingSignalProvider` composes a caller-owned `FilingDocumentLoader` with `SecDocumentSignalParser`, which strips script/style blocks and markup, never executes content, rejects oversized documents, and matches only explicit phrases. Document enrichment is capped by `max_signal_documents` per snapshot. The loader remains an external I/O port so archive access, caching, byte limits, and request policy can be implemented and tested independently.

CompanyFacts mapping is conservative:

- shares outstanding require a current and approximately year-prior pair;
- quarterly operating cash flow requires a 70–110 day duration;
- cash and operating cash flow must have period ends within ten days;
- facts filed after `as_of` are ignored.

If a comparable pair cannot be established, the adapter omits that metric instead of fabricating or annualizing it.
