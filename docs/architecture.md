# Architecture

MarketBot is a modular monorepo organized around independently evolving engines. Each engine owns
its code, tests, and local documentation under `app/<engine>/`.

## Dynamic universe boundary

The Core universe is the active PostgreSQL watchlist plus positive holdings. The coordinator
publishes `UniverseChanged` on `marketbot.v1.universe.changed.core`. Long, Swing, and Intraday may
collect bars while a symbol is warming, but `consumer_warmup_required=true` prevents publication
until their own horizon history is loaded and the snapshot is activated. Legacy definitions remain
open when no universe event is configured.

Every engine readiness/health payload declares `universe_policy` and `warmup_policy`, making Core,
holdings-only, tagged `PORT_YTD`, fixed rotation, and registered-watchlist universes explicit.

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

MarketBot has one composition source: `configs/marketbot/7.9.0.yaml`. It declares every engine
slot, the concrete implementation version, the strategy version and artifact, and its operational
mode. `app/integration/engine_catalog.py` is the concrete implementation catalog and
`app/integration/engine_assembly.py` is the stable selector/facade.

```text
configs/marketbot/7.9.0.yaml
  implementation + strategy + mode
                 |
                 v
        MarketBotAssembly
          | validates definition-level requirements
          | selects an EngineRegistration
          v
        engine-owned strategy adapter
          | validates its own business-rule keys
          | translates the artifact into constructor options
          | builds the selected implementation
          |
          +--> local live runtime / analyzer
          +--> Long / Swing / Intraday workers
          +--> Entry Watcher / Entry Opportunity / Alert
          +--> Portfolio Flow / Rotation / LONG Portfolio
          +--> Patreon / Elliott / Support / Fusion
          +--> Options Gamma
          +--> SEC / Peter Lynch
```

An engine implementation is code; a strategy is the rule set used by that code; a mode describes
how it participates operationally (`active`, `scheduled`, or `on-demand`). These are
separate coordinates and are reported separately in readiness/one-shot summaries. No worker has a
fallback constructor: a composition must receive its engine from `MarketBotAssembly`.

`app/integration/runtime_process_plan.py` is the single distributed topology source. It derives the
active processes from the selected definition and owns process names, command arguments, readiness
files, dependency edges, and deterministic parallel startup batches. Windows and Linux consume the
same `marketbot runtime-plan` JSON; their scripts own only platform supervision and window/tmux
presentation. Operator monitors are explicitly separated from headless readiness, so they cannot
gate the market stream. `scheduled` engines remain owned by their external scheduler and
`on-demand` engines remain available through their explicit operator command.

Options Gamma is an active headless, read-only Alpaca process. It refreshes the Core universe at a
bounded interval and publishes both `GammaAssessment` and `AnalysisResult(OPTIONS_GAMMA)`. Alert,
Entry Watcher, and Signal Fusion consume only the stable analysis contract. Missing, stale, or
low-quality Gamma context contributes zero; the process is deliberately not a readiness dependency
of the Alpaca equity stream.

The lightweight YAML model and mode loader live in `marketbot_definition.py`. The generic registry
and lifecycle metadata live in `engine_registry.py`; the root catalog only maps slots and versions
to concrete classes. Strategy interpretation for Swing, Intraday, Entry Watcher, Alert, Entry
Recovery, Portfolio Flow, Long Portfolio, and Patreon Caps lives in each owning engine.
`MarketBotAssembly` therefore has no
business-rule key or implementation-version branch. Operator commands are likewise registered by
focused runtime and infrastructure modules instead of accumulating every concern in `main.py`.

Every registration declares `required_since`, the first MarketBot definition version in which its
slot is mandatory. Old immutable definitions remain valid when a later engine is introduced; no
special-case list of historical definition versions is maintained. Integration code may use the
generic `assembly.build(slot, ...)` path, while typed `build_*` methods remain as compatibility
facades for current compositions.

When a composition needs policy metadata before construction—for example, a rule version used to
restore state or Patreon macro symbols—it calls `assembly.resolve_strategy(slot, runtime_inputs)`.
The engine-owned adapter parses and validates the artifact. Compositions may consume the resulting
typed policy, but they never open strategy YAML files or construct policy objects themselves.

The catalog may contain old implementations for rollback, but the definition chooses exactly one
per slot. Consumers depend on stable event capabilities, never on a producer's concrete
implementation version. `engine_version` remains provenance for audit and metrics, not a routing
or compatibility gate. Portfolio Flow therefore has real V1 and V2 implementations and matching
policy artifacts; selecting V1 restores sell-only behavior without modifying V2.

Swing, Intraday, Entry Watcher, Alert, and Entry Recovery resolve independent strategy artifacts.
Changing one strategy does not require editing the other engines' definitions. Released definition
and rule YAML files are append-only: CI rejects modification, deletion, or rename and requires a new
SemVer file for every reviewed change.

Entry Recovery 1.1 publishes `EntrySetupAssessment(CORE_RECOVERY)` without maturity. Alert 3.2
consumes that stable contract and owns the configured quality decision (`SWING + INTRADAY -> L2`),
then publishes the confirmed `EntrySignal CORE_RECOVERY` for Opportunity. Recovery 1.0 and Alert
3.1 remain selectable as the previous rollback flow.

## Distributed analysis-only MVP flow

```text
Alpaca market stream
        |
        v
Long / Swing / Intraday ---------------------> AnalysisResult v1
                                                      |
                           +--------------------------+-------------------+
                           |                                              |
                           v                                              v
                    Alert 3.2 <------------------------------- Entry Watcher 5.1
              LocalAlert + EntrySignal L1-L4                  PostgreSQL state + outbox
                                                                          |
                                                                  Watcher transition

Patreon / Long Portfolio / Fusion / Portfolio Flow -------> EntrySignal by family
                                                                          |
AnalysisResult + Watcher lifecycle + EntrySignal + 1m bars ---------------+
                                                                          v
                                                              Entry Opportunity 3.0
                                                              PostgreSQL state + outbox
                                                                          |
                                             leg invalidation ------------+
                                                                          v
                                                              Entry Recovery 1.1
                                                       EntrySetupAssessment (no level)
                                                                          |
                                                                          v
                                                                    Alert 3.2
                                                        CORE_RECOVERY EntrySignal L2

PostgreSQL outbox --> headless outbox relay --> NATS JetStream --> read-only tmux monitors

Alpaca options snapshots --> Options Gamma --> AnalysisResult(OPTIONS_GAMMA)
                                                   |       |
                                                   v       v
                                              Alert 3.5  Signal Fusion 0.5
```

Backfill never traverses the live bar subjects. Market History owns REST coverage and workers do not
emit decisions for a newly-added symbol until their declared warmup is complete. The launcher starts
all business processes headlessly; market ingress waits only for required engine readiness. A tmux
monitor can exit or restart without stopping an engine or blocking market data.

`EntrySignal` is the stable analytical decision contract. Consumers use its family, setup, optional
core L1-L4 maturity, levels, and policy provenance; they never route on a producer implementation
version. Patreon Caps, Long Portfolio, Signal Fusion, and Portfolio Flow retain distinct families
and therefore cannot be aggregated as fake core L4 decisions. Watcher `TRIGGERED` is the canonical
core L4 decision identity, but Alert is its only publisher. Entry Recovery is a separately
versioned evidence engine: it never assigns L1-L4, relaxes, or rewrites the original Watcher
invalidation. Alert evaluates its recovery assessment and the current Swing+Intraday rule assigns
L2; changing that quality requires a new Alert rule version.

Watcher 5.4 adds two non-terminal lifecycle states without changing that L4 identity.
`EARLY_ENTRY` opens a paper L1 leg with its own tactical invalidation and target;
`IMPULSE_EXTENDED` persists a missed impulse while a dynamic pullback is observed. A later mature
confirmation may still advance the same frozen thesis to canonical `TRIGGERED` L4.

The `Compras Confirmadas` operator window is a focused projection, not another decision engine. It
renders only core L1-L4 decisions and final Patreon Caps, Long Portfolio, and Signal Fusion buys
from `EntrySignal`. It does not render Opportunity lifecycle progress, which remains in the
Opportunity window. Portfolio Flow shares the screen in a visibly separate manual-management lane:
cyan aggressive-buy watches and red `PROTECT` alarms remain `LocalAlert` notifications and never
become confirmed buys or acquire an L1-L4 label.

Entry Opportunity 3.0 materializes paper-only lifecycles. It orders inputs independently by source,
tracks separate horizon legs, closes the aggregate when all opened horizons terminate, and bounds
analysis provenance. Non-material analysis refreshes update the materialized snapshot without
appending another full historical event. A standalone analytical family may create its own paper
opportunity when the signal provides complete entry-zone and invalidation levels.

Watcher and Opportunity commit state, lifecycle evidence, and their NATS envelope to PostgreSQL in
one short transaction. A separate relay claims committed outbox rows with `FOR UPDATE SKIP LOCKED`,
publishes outside the transaction, and records success or exponential-backoff retry. Delivery is
at-least-once and event IDs remain stable for consumer idempotency.

SEC dilution analysis runs in an independent once-daily process. Its adapter filters an inclusive
recent filing-date window and relevant forms before optional CompanyFacts work; it is never queried
during realtime startup or for each market tick.

The integration layer asks the shared assembly for one concrete engine inside its dedicated
process, but no process imports or invokes another engine. Every output crosses the shared `MarketBar`, `AnalysisResult`,
`LocalAlert`, `ServiceHealth`, or `EventEnvelope` boundary.
There is no Trading API, order intent, position sizing, or account state in this composition.
Every `BUY_CONFIRMED`, PatreonCaps signal, and L1-L4 signal is analytical. Entry Opportunity records,
tracks, and closes paper trades in PostgreSQL to measure effectiveness and gain/loss. A future
broker executor must be a separate opt-in consumer of stable confirmed-signal contracts.

## Universe policies

| Process | Operational universe |
| --- | --- |
| Long / Swing / Intraday | active watchlist plus positive holdings, after per-engine warmup |
| Entry Watcher / Alert | stable analysis events from the core universe |
| Entry Opportunity / Recovery | active paper opportunities derived from lifecycle/signals |
| Patreon Caps | complete core universe; holdings affect analytical sizing context only |
| Elliott / Support / Signal Fusion | positive holdings only |
| Long Portfolio | configured `PORT_YTD` allocation symbols |
| Portfolio Flow | live holdings, trades, and quotes |
| Rotation | configured sectors, profiles, and proxies |
| SEC / Peter Lynch | registered universe, scheduled or on demand |

Universe refreshes publish `UniverseChanged`; services report both `universe_policy` and
`warmup_policy` in health so a new symbol cannot silently receive partial evidence.

## Runtime durability

- Each engine's live working set exists only in that engine process.
- Durable NATS consumers are distinct per engine; they are not a shared queue group, so every
  required engine receives its own copy of each live bar.
- NATS JetStream retains live market updates, engine results, service health, and final alerts.
- PostgreSQL outbox rows bridge durable state and JetStream without a DB-to-NATS dual-write gap.
- Support Confirmation persists holdings-only assessments and state transitions in JetStream. It
  is an analytical producer and has no dependency edge into PatreonCaps, ElliottWave, or Alert.
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
