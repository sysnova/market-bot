# Alert engine ownership

- This folder owns human-notification aggregation, local sinks, tests, fixtures, and docs.
- Consume only shared `AnalysisResult` values and emit only shared `LocalAlert` values.
- Never express orders, sizing, positions, accounts, or Trading API operations.
- Time is always an explicit input; do not read the process clock inside policy code.
- Transport publication is a port. Console and NDJSON are local sink adapters.
- Preserve deterministic scoring, freshness, cooldown, and deduplication behavior.
- Add a failing unit test before implementation.
