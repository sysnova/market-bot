# Foundation integration

This package is the repository-owned composition boundary. It may import concrete
engines to prove the whole milestone, while individual engines remain coupled only
through contracts and ports.

`prepare_foundation_engine` discovers the trusted synthetic rule pack, freezes one
PAPER registry snapshot, compiles PRIMARY and SHADOW strategies, executes them on
the same context, and durably audits traces and decisions as idempotent NDJSON.

The live composition also connects the pure horizon engines to `EntryWatcher`. Analysis results
remain the only cross-engine input. The watcher persists through the PostgreSQL adapter, while
`AnalysisRuntime` converts its lifecycle transitions into local human alerts. Failure to connect
to the optional watcher database is isolated from Alpaca analysis and NATS mirroring.
