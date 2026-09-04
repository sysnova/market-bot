# Entry Opportunity Engine

## Independent strategy exits (v10, MarketBot 7.48.0)

Core continuation and SwingTrade Fibonacci recovery have different premises. A bearish
Core Swing/Long verdict is context for SwingTrade and GERI countertrend, not authority
to close their entries. This policy also applies when multiple families share a ticker.
Core analyses and matching Entry Watcher terminal events close only Core-owned entries;
`CORE_RECOVERY` remains part of the Core L2/retest circuit, distinct from `SWING_TRADE`.
No bullish Core consensus has been added to the ST/CT entry gates.

Ownership is resolved from each checkpoint's family and each leg's explicit family or setup provenance;
new Core legs never reuse another family's horizon leg. Legacy unlabelled primary legs
retain the original primary family. Ambiguous setup
provenance does not grant Core exit authority. Analysis evidence must be at least as
recent as the entry it closes. Historical context remains visible without replacing a
newer price mark. Invalidated checkpoints retain `INVALIDATED`/`THESIS_BROKEN`, not
`TIME_EXIT`, even if that closes the final Core horizon.

Independent checkpoints without a dedicated leg also survive Core closure and subsequent
market bars. Their own price stops, targets, and expiration policies remain active.
Universe removal remains a separate global administrative closure. Existing closed audit
events are not rewritten or reopened by this upgrade; historical performance requires
a separately labelled replay with subsequent market data, not relabelling old exits as
profitable trades. Assemblies through 7.47.0 retain the v9 ownership policy for rollback.

`EntryOpportunityEngine v1` is the lifecycle and paper-trade engine for candidate buys. It
merges Entry Watcher transitions, L1-L4 alerts, analysis results, and final one-minute bars
into one active opportunity per ticker while preserving the original thesis.

It owns maturity progress, horizon legs, invalidation/session closure, gain/loss, MFE/MAE,
and immutable audit events. In distributed operation it runs as `entry-opportunity-v1` and
publishes `marketbot.v1.entry-opportunity.event.>` for Alert Engine and the confirmed-buy
monitor.

Watcher and alert inputs advance with independent durable causal cursors, so delayed delivery
cannot be rejected by an unrelated newer bar or analysis. Analysis provenance in the materialized
snapshot is bounded to 32 identifiers (the original plus the most recent 31); only material
lifecycle changes append full audit events. When every opened horizon leg becomes terminal, the
opportunity closes with `ALL_HORIZONS_CLOSED`.

`EntryOpportunityEngineV2` adds direct `EntrySignal` ingestion. Core families advance L1-L4;
analytical families are recorded with `maturity=None` and never promoted to a core L-level. A
source-agnostic signal with complete zone/invalidation levels can create its own standalone paper
opportunity even when no Entry Watcher root exists. Signal IDs and `(family, setup_id)` provide
idempotency; the compact references are bounded to 32 setups. `ingest_alert` remains only as a
temporary compatibility adapter for older in-process callers.

`EntryOpportunityEngineV3` mirrors the watcher's current tracking state, so a zone exit can
move `current_maturity` and status from `IN_ZONE` back to `ARMED`. `peak_maturity` remains
`IN_ZONE`, preserving the highest stage reached, and L1-L4 confirmation never regresses.

Each accepted final one-minute bar advances a persisted `last_market_bar_at` cursor. The
distributed process subscribes to live bars in buffer mode, forces Market History to reconcile
the latest REST tail, replays only PostgreSQL bars after that cursor, and then drains buffered live
bars in timestamp order. This makes restart recovery independent of retained JetStream bar history;
duplicate or older bars are ignored.

`EntryOpportunityEngineV4` adds the independent SwingTrade `ST1-ST4` lane. Version `5.0.0`
adds the GERI tactical countertrend family with a separate `CT0-CT4` maturity: `CT0` is a
reference-only watch, `CT1` opens the paper Swing leg, and `CT2-CT4` add measurement checkpoints.
It records P/L, MFE/MAE, invalidation, target and a five-session TTL without changing any Core
`L1-L4` or SwingTrade `ST1-ST4` maturity. It never submits broker orders.
Standalone GERI opportunities can only be created from regular-session signals. CT0 favorable
extension or reward/risk loss is tracked until the final regular minute, while target and
invalidation remain immediate; CT0 output is a reference move, not trade P/L.

Commands:

```bash
uv run marketbot entry-opportunity serve
uv run marketbot entry-opportunity report
uv run marketbot entry-opportunity prune-history --older-than-days 30
```

History pruning is dry-run by default. It only targets old single-reason legacy
`*_evidence_updated` events, preserves the newest configured count per opportunity, and requires
`--apply` before it deletes bounded batches. It never runs `VACUUM`, `VACUUM FULL`, or `pg_repack`.
