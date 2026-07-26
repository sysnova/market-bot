# Reference engine ownership

- This folder owns reference-event orchestration, its ports, tests, and focused docs.
- Do not import concrete audit, registry, rule-pack, persistence, or transport adapters here.
- Keep `EvaluationContext` construction single-shot and immutable for every input event.
- Preserve deterministic decision IDs and event-level deduplication on all changes.
- `SHADOW` must never become eligible. `PRIMARY` requires an accepted trace and confirmed audit.
- Add a failing unit test before implementation. Unit tests require no network or services.
- Cross-module wiring and replay scenarios belong in `app/integration/`.
