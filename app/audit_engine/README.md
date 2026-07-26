# Audit engine

The audit engine durably stores the immutable `EventEnvelope` produced by other
engines. It is an adapter boundary: business engines never import this package.

## Layout

Each event is routed to:

```text
runtime/YYYY-MM-DD/runs/<run_id>/services.ndjson
runtime/YYYY-MM-DD/runs/<run_id>/rule-traces.ndjson
runtime/YYYY-MM-DD/runs/<run_id>/decisions.ndjson
```

The envelope payload carries routing metadata under
`{"audit": {"run_id": "...", "stream": "services|rule-traces|decisions"}}`.
An event is acknowledged only after its canonical NDJSON line has been written
and `fsync` has completed. When a directory or audit file is first created, its
parent directory is also fsynced before confirmation. Directory fsync is
best-effort on Windows, where opening a directory handle can be unsupported;
the file fsync remains mandatory. Re-delivery is idempotent by `event_id`.

At startup the engine rebuilds its event index. An unterminated final line is
removed as a crashed append; corruption in any completed line stops startup.
Only one `AuditLog` instance in a process may write a given file at once.

## Entrypoint

```powershell
uv run python -m app.audit_engine --runtime-root runtime
```

This validates/recover logs and exits. Long-running operation is composed by the
root supervisor through the shared `EventBus` port. The logical subscription is
`audit.>`; adapters qualify it with their configured prefix (normally
`marketbot`). The stable `audit-engine-v1` durable requests replay from the
beginning so an engine restart does not skip unacknowledged backlog. The bus
considers a handler's successful return to be its delivery acknowledgement, so
storage or validation failures propagate and remain eligible for redelivery.

The service depends only on the shared bus port and subscription options, never
on a concrete NATS or in-memory adapter.
