# Strategy runtime

This shared, in-process library turns a strict `StrategySpec` into an immutable executable
plan and evaluates that plan without coupling to a rule pack implementation.

## Compile

`load_strategy_yaml` uses `yaml.safe_load` and Pydantic's strict JSON validation. The compiler
requires a registry snapshot frozen for the same run, resolves every declared
`(rule_id, rule_version)` coordinate exactly, validates static parameters through the provider's Pydantic model, verifies
manifest and metadata hashes, and emits a stable topological order. Scoring weights are already
guarded by the v1 contract and are never normalized by this package.

The definition digest omits `run_id`. The compiled-plan digest contains semantic strategy
content, exact implementations, bindings, dependencies, and policies; it omits compilation
time, trace IDs, process IDs, durations, and filesystem paths.

## Execute

Each enabled rule runs in a fresh spawned subprocess. A hard deadline terminates the process,
then kills it if necessary, and always joins it. An optional RSS ceiling is monitored with
`psutil`. Exceptions, timeouts, malformed returns, and identity drift become `RuleResult(ERROR)`
instead of escaping into the engine.

Dependency policies and fail/error/NOT_APPLICABLE policies are applied before the exact Decimal
weighted sum. `CANDIDATE` and `RESEARCH` execute but are never action-eligible. `DISABLED` produces
a no-decision trace without starting a rule. An accepted `PRIMARY` result becomes eligible only
after `AuditSink.confirm(trace)` returns true. Execution and trace UUIDs are deterministically
derived from the compiled plan and evaluation context for idempotent retries.

## Ports

Providers and registry snapshots are structural protocols in `ports.py`. A composition root may
pass `app.rule_registry.RegistrySnapshot` and trusted entry-point providers directly, while this
package does not import either their concrete implementation or a rule pack.
