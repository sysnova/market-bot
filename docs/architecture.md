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

## Distributed analysis-only MVP flow

```text
                    Alpaca WebSocket process
                              |
                              v
                       NATS JetStream
                    /         |         \
                   v          v          v
            Long process  Swing process  Intraday process
             own REST       own REST        own REST
             own store      own store       own store
                   \          |          /
                    \         v         /
                     AnalysisResult events
                         /          \
                        v            v
              Entry Watcher v3   Alert v2 process
                 PostgreSQL         ^
                        |            |
                        +-- transitions
                              |
                  final alerts over NATS
                              |
                 console + NDJSON + viewers

Daily scheduler -> bounded SEC EDGAR scan -> Dilution -> alerts + NDJSON
```

Backfill never traverses NATS. Each engine asks Alpaca REST only for its own working set and owns a
private `MarketBarStore`: Long loads 260 daily and 220 weekly bars; Swing loads 120 daily and 160
15-minute bars; Intraday loads 500 minute bars. Swing derives live 15-minute bars locally and
Intraday derives live 5-minute bars locally. The launcher starts the WebSocket process only after
the three engine consumers, Alert, and Entry Watcher report readiness.

Alert v2 is a separate consumer. It keeps the latest fresh result by ticker and horizon, but never
reads an engine's store or recalculates indicators. A bullish Long result can emit `LONG_BUY_ZONE`,
a bullish Swing result can emit `SWING_SETUP`, Intraday plus Long or Swing can emit
`ENTRY_CONFIRMED`, and all three bullish engines can emit `HIGH_CONVICTION_BUY`.
Entry Watcher v3 is another independent consumer of the same results. It freezes Long entry zones
in PostgreSQL and publishes lifecycle transitions back to NATS; Alert v2 renders those transitions
without reading the watcher database.
SEC dilution analysis runs in an independent once-daily process. Its adapter filters an inclusive
recent filing-date window and relevant forms before optional CompanyFacts work; it is never queried
during realtime startup or for each market tick.

The integration layer may compose one concrete engine inside its dedicated process, but no process
imports or invokes another engine. Every output crosses the shared `MarketBar`, `AnalysisResult`,
`LocalAlert`, `ServiceHealth`, or `EventEnvelope` boundary.
There is no Trading API, order intent, position sizing, or account state in this composition.

## Runtime durability

- Each engine's live working set exists only in that engine process.
- Durable NATS consumers are distinct per engine; they are not a shared queue group, so every
  required engine receives its own copy of each live bar.
- NATS JetStream retains live market updates, engine results, service health, and final alerts.
- Support Confirmation persists holdings-only assessments and state transitions in JetStream. It
  remains a SHADOW producer and has no dependency edge into PatreonCaps, ElliottWave, or Alert v2.
- Signal Fusion consumes the stable NATS outputs from Support Confirmation, Elliott Wave, Long,
  Swing, Intraday, dilution SEC, and PatreonCaps. It does not import or invoke those engines.
  PatreonCaps is derived context and is never counted as another independent Long/Swing vote.
  Fusion publishes its own assessment, transition, and SHADOW buy-confirmed subjects.
- REST historical bars stay process-local and are reloaded independently after a restart.
- Local alerts are fsync'd as canonical NDJSON, rotated by New York market date, and deduplicated
  across restarts within each daily ledger.
- PostgreSQL stores durable entry theses and transitions. Analytical indicator calculation remains
  in each engine's memory; no engine queries PostgreSQL for every tick.

## Determinism

Time and entropy are injected at boundaries. Persisted or signed payloads use canonical JSON and a
SHA-256 content digest. Identifiers are UUIDv7 so their leading 48 bits preserve millisecond creation
order while retaining RFC-compatible randomness.

## Configuration and observability

Runtime settings use Pydantic and the `MARKETBOT_` environment namespace. Secrets use `SecretStr`
and diagnostic representations are redacted. Structlog emits JSON by default and supports bound
correlation context; local development may opt into human-readable output.
