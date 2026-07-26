# Strategy runtime ownership

- This folder owns safe strategy loading, deterministic compilation, policy evaluation,
  subprocess rule isolation, and the audit-confirmation gate.
- Keep this package in-process. Do not add HTTP, NATS, PostgreSQL, or broker consumers.
- Depend on `app/contracts` and structural ports only; never import a concrete engine or rule pack.
- Rule execution must remain fail-closed for PRIMARY and must always reap child processes.
- Definition hashes exclude operational run IDs and all timing data.
- Contract gaps must be recorded as an RFC for root compatibility review; do not modify
  `app/contracts` from this folder.

