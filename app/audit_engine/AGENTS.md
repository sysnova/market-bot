# Audit engine ownership

This folder owns the audit service, NDJSON storage, tests, and focused docs.

- Keep records append-only and canonical; never rewrite completed lines.
- Preserve idempotency by `EventEnvelope.event_id` across restarts.
- Only acknowledge bus delivery after the record has been fsynced or identified
  as an existing duplicate.
- Treat only an unterminated final line as recoverable crash residue. Fail fast on
  completed-line corruption.
- Do not import another engine. Integrate through contracts and structural ports.
- Add a failing isolated test before changing recovery, replay, or durability.
