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

## Engine assembly

MarketBot has one composition source: `configs/marketbot/6.0.0.yaml`. It declares every engine
slot, the concrete implementation version, the strategy version and artifact, and its operational
mode. `app/integration/engine_assembly.py` is the only implementation catalog and factory.

```text
configs/marketbot/6.0.0.yaml
  implementation + strategy + mode
                 |
                 v
        MarketBotAssembly
          | validates every slot
          | rejects unknown versions
          | validates each selected implementation independently
          | resolves immutable strategy artifacts
          |
          +--> local live runtime / analyzer
          +--> Long / Swing / Intraday workers
          +--> Entry Watcher / Entry Opportunity / Alert
          +--> Portfolio Flow / Rotation / LONG Portfolio
          +--> Patreon / Elliott / Support / Fusion
          +--> SEC / Peter Lynch
```

An engine implementation is code; a strategy is the rule set used by that code; a mode describes
how it participates operationally (`active`, `scheduled`, or `on-demand`). These are
separate coordinates and are reported separately in readiness/one-shot summaries. No worker has a
fallback constructor: a composition must receive its engine from `MarketBotAssembly`.

The catalog may contain old implementations for rollback, but the definition chooses exactly one
per slot. Consumers depend on stable event capabilities, never on a producer's concrete
implementation version. `engine_version` remains provenance for audit and metrics, not a routing
or compatibility gate. Portfolio Flow therefore has real V1 and V2 implementations and matching
policy artifacts; selecting V1 restores sell-only behavior without modifying V2.

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
                       /       |       \
                      v        v        v
          Entry Watcher v4  Alert v2  Entry Opportunity v1
             PostgreSQL       ^          PostgreSQL
                  |            |              ^
                  +-- watcher transitions ----+
                  |                           |
                  +------ local alerts -------+
                                              |
                          progress/closures over NATS
                                              |
                            Alert v2 + confirmed-buy viewer

Daily scheduler -> bounded SEC EDGAR scan -> Dilution -> alerts + NDJSON
```

Backfill never traverses NATS. Each engine asks Alpaca REST only for its own working set and owns a
private `MarketBarStore`: Long loads 260 daily and 220 weekly bars; Swing loads 120 daily and 160
15-minute bars; Intraday loads 500 minute bars. Swing derives live 15-minute bars locally and
Intraday derives live 5-minute bars locally. The launcher starts the WebSocket process only after
the three engine consumers, Alert, Entry Watcher, and Entry Opportunity report readiness.

Alert v2 is a separate consumer. It keeps the latest fresh result by ticker and horizon, but never
reads an engine's store or recalculates indicators. A bullish Long result can emit `LONG_BUY_ZONE`,
a bullish Swing result can emit `SWING_SETUP`, Intraday plus Long or Swing can emit
`ENTRY_CONFIRMED`, and all three bullish engines can emit `HIGH_CONVICTION_BUY`.
Entry Watcher v4 is another independent consumer of the same results. It freezes Long entry zones
in PostgreSQL and publishes lifecycle transitions back to NATS; Alert v2 renders those transitions
without reading the watcher database. Intraday v4 keeps extended first impulses in `WATCH`, and
Entry Watcher v4 requires a second fresh mature confirmation before triggering.
Entry Opportunity v1 is its own assembly slot and independent NATS process. It consumes watcher
transitions, analyses, L1-L4 local alerts, and
final market bars. It materializes one active root per ticker, keeps separate Intraday/Swing/Long
paper legs, closes Intraday at the regular-session end, and appends immutable gain/loss evidence.
Its minute reconciler also expires or closes symbols removed from the active universe even when no
new analysis arrives.
SEC dilution analysis runs in an independent once-daily process. Its adapter filters an inclusive
recent filing-date window and relevant forms before optional CompanyFacts work; it is never queried
during realtime startup or for each market tick.

The integration layer asks the shared assembly for one concrete engine inside its dedicated
process, but no process imports or invokes another engine. Every output crosses the shared `MarketBar`, `AnalysisResult`,
`LocalAlert`, `ServiceHealth`, or `EventEnvelope` boundary.
There is no Trading API, order intent, position sizing, or account state in this composition.
Every `BUY_CONFIRMED`, PatreonCaps buy, and L1-L4 alert is analytical. Entry Opportunity records,
tracks, and closes paper trades in PostgreSQL to measure effectiveness and gain/loss. A future
broker executor must be a separate opt-in consumer of stable confirmed-signal contracts.

## Runtime durability

- Each engine's live working set exists only in that engine process.
- Durable NATS consumers are distinct per engine; they are not a shared queue group, so every
  required engine receives its own copy of each live bar.
- NATS JetStream retains live market updates, engine results, service health, and final alerts.
- Support Confirmation persists holdings-only assessments and state transitions in JetStream. It
  is an analytical producer and has no dependency edge into PatreonCaps, ElliottWave, or Alert v2.
- Signal Fusion consumes the stable NATS outputs from Support Confirmation, Elliott Wave, Long,
  Swing, Intraday, dilution SEC, and PatreonCaps. It does not import or invoke those engines.
  PatreonCaps is derived context and is never counted as another independent Long/Swing vote.
  Fusion publishes its own assessment, transition, and analytical buy-confirmed subjects.
- Portfolio Flow v2 consumes ephemeral quotes and trades. It emits red `PROTECT` alerts for
  concentrated selling and cyan `AGGRESSIVE ENTRY WATCH` alerts for concentrated buying at the
  ask. Buy pressure remains an early observation and does not acquire an L1-L4 maturity by itself.
- Market History is the single owner of Alpaca REST historical bars. Engines request missing
  coverage through NATS Core, then load the shared bounded cache from local PostgreSQL. Historical
  bars and RPC requests are not retained by JetStream.
- The WebSocket ingress publishes live bars through NATS only. Market History reconciles recent
  REST coverage once per hour and after an engine restart.
- Local alerts are fsync'd as canonical NDJSON, rotated by New York market date, and deduplicated
  across restarts within each daily ledger.
- PostgreSQL stores bounded historical bars, durable entry theses, and transitions. Engines read
  history only during bootstrap/refresh; analytical calculation and live bars remain in memory, so
  no engine queries PostgreSQL for every tick.

## Determinism

Time and entropy are injected at boundaries. Persisted or signed payloads use canonical JSON and a
SHA-256 content digest. Identifiers are UUIDv7 so their leading 48 bits preserve millisecond creation
order while retaining RFC-compatible randomness.

## Configuration and observability

Runtime settings use Pydantic and the `MARKETBOT_` environment namespace. Secrets use `SecretStr`
and diagnostic representations are redacted. Structlog emits JSON by default and supports bound
correlation context; local development may opt into human-readable output.

`marketbot assembly` prints the effective definition, including resolved artifacts. The selected
definition can be changed with `MARKETBOT_DEFINITION_PATH`; the old
`MARKETBOT_ENTRY_CONFIRMATION_RULE_VERSION` setting remains only as a deprecated atomic rollback
override. New definitions should select each engine and strategy version independently.
