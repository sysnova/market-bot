# Foundation integration

This package is the repository-owned composition boundary. It may import concrete
engines to prove the whole milestone, while individual engines remain coupled only
through contracts and ports.

`prepare_foundation_engine` discovers the trusted synthetic rule pack, freezes one
PAPER registry snapshot, compiles PRIMARY and SHADOW strategies, executes them on
the same context, and durably audits traces and decisions as idempotent NDJSON.

`distributed_composition.py` is the active process boundary for the analytical MVP. It creates one
private store and one v2 engine per process, performs horizon-specific REST bootstrap, subscribes
that process to its own durable NATS subjects, and publishes only `AnalysisResult` values. The
Alpaca WebSocket, persistent Entry Watcher v3, and Alert v2 aggregation have their own process
composition roots. Entry Watcher consumes results, persists its state in PostgreSQL, and publishes
transitions through NATS; Alert consumes both results and transitions.

The legacy live composition also connects the pure horizon engines to `EntryWatcher`. Analysis results
remain the only cross-engine input. The watcher persists through the PostgreSQL adapter, while
`AnalysisRuntime` converts its lifecycle transitions into local human alerts. Failure to connect
to the optional watcher database is isolated from Alpaca analysis and NATS mirroring.

The active distributed analytical generation is selected explicitly in
`distributed_composition.py`: Long v2, Swing v3, Intraday v3, Entry Watcher v3, and Alert v2. Earlier classes and the
legacy single-process composition remain available for diagnostics; changing an active generation
must never silently rewrite an old class.
