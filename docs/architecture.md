# Architecture

MarketBot is a modular monorepo organized around independently evolving engines. Each engine owns
its code, tests, and local documentation under `app/<engine>/`.

## Boundaries

```text
operator / external input
          |
          v
   stable contracts  <---->  engine A
          ^                   (private model)
          |
          +--------------->  engine B
                              (private model)
```

An engine never imports another engine. Cross-engine communication uses stable types and ports from
`app/contracts/`, normally transported over NATS JetStream. PostgreSQL is durable infrastructure;
schema ownership must remain explicit rather than becoming an implicit shared model.

`app/common/` contains technical primitives only: clock injection, IDs, canonical encoding,
configuration, and logging. It must not become a dumping ground for shared business behavior.

## Analysis-only MVP flow

```text
Alpaca REST backfill + realtime WebSocket
                 |
                 v
      MarketBar / trade / quote events
                 |
          local event fan-out -----------------> NATS JetStream
                 |
          bounded bar store
            /    |     \
      Long-term Swing Intraday
            \    |     /
             AnalysisResult events
                       |
                 Alert Engine
                       |
          console + append-only NDJSON

Daily scheduler -> bounded SEC EDGAR scan -> Dilution -> alerts + NDJSON
```

Backfill is quiet: it warms the store before live reactions are enabled. Intraday evaluates each
completed 1-minute bar. The root aggregates completed 1-minute bars into 5-minute and 15-minute
bars; Swing evaluates on each completed 15-minute bar. Long-term evaluates on daily/weekly updates.
SEC dilution analysis runs in an independent once-daily process. Its adapter filters an inclusive
recent filing-date window and relevant forms before optional CompanyFacts work; it is never queried
during realtime startup or for each market tick.

The root composition may import and invoke engines, but engines never import one another. Every
output crosses the shared `MarketBar`, `AnalysisResult`, `LocalAlert`, or `EventEnvelope` boundary.
There is no Trading API, order intent, position sizing, or account state in this composition.

## Runtime durability

- The live working set and latest cross-horizon analyses are held in memory for low-latency rules.
- The realtime in-process bus disables replay history and event-ID retention during backfill, and
  delivers locally with backpressure so a large watchlist cannot create an unbounded task queue.
- NATS JetStream durably mirrors market, analysis, and alert events.
- Local alerts are fsync'd as canonical NDJSON, rotated by New York market date, and deduplicated
  across restarts within each daily ledger.
- PostgreSQL remains available for future run/audit/query models; the MVP does not put PostgreSQL
  in the per-tick decision path.

## Determinism

Time and entropy are injected at boundaries. Persisted or signed payloads use canonical JSON and a
SHA-256 content digest. Identifiers are UUIDv7 so their leading 48 bits preserve millisecond creation
order while retaining RFC-compatible randomness.

## Configuration and observability

Runtime settings use Pydantic and the `MARKETBOT_` environment namespace. Secrets use `SecretStr`
and diagnostic representations are redacted. Structlog emits JSON by default and supports bound
correlation context; local development may opt into human-readable output.
