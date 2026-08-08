# Reference engine

The reference engine is the smallest runnable consumer that proves two exact strategy
versions can evaluate the same event without sharing mutable state. It accepts prepared
strategies through a structural boundary, creates one frozen `EvaluationContext`, evaluates
the configured `PRIMARY` and `CANDIDATE`, and emits an idempotent `EngineEvaluation` per version.

The engine does not import a concrete rule pack, registry, audit store, or event transport.
The root integration layer owns those adapters. `PreparedStrategy` carries the immutable
definition, plan, and registry hashes plus an evaluation callback. The callback is where the
composition root connects `StrategyCompiler`, `StrategyRuntime`, and an `AuditSink`.

Eligibility is guarded twice. Only an accepted `PRIMARY` result with a positive durable audit
confirmation can be eligible. A `CANDIDATE` result is always forced to non-eligible, even if a
faulty adapter claims otherwise. Event redelivery returns the cached result; decision IDs are
deterministic from the event, strategy version, and compiled-plan hash. Result sinks must also
deduplicate by decision ID so retries remain safe when a sink fails mid-batch.

Input envelopes use a mapping payload with `symbol`, `timeframe`, and `values`. The envelope's
`occurred_at` and market session become the evaluation instant and session. Value names are
sorted before the context is frozen, making its content hash reproducible.

Run the package boundary with:

```powershell
uv run python -m app.reference_engine --help
```

The concrete local process is wired by `app/integration`; production keeps transports and
engines in separate processes.
