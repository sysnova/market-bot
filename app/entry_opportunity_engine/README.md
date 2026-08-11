# Entry Opportunity Engine

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

Commands:

```bash
uv run marketbot entry-opportunity serve
uv run marketbot entry-opportunity report
uv run marketbot entry-opportunity prune-history --older-than-days 30
```

History pruning is dry-run by default. It only targets old single-reason legacy
`*_evidence_updated` events, preserves the newest configured count per opportunity, and requires
`--apply` before it deletes bounded batches. It never runs `VACUUM`, `VACUUM FULL`, or `pg_repack`.
