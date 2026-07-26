# Event bus ownership

- This folder owns event transport ports, subject semantics, codecs, and the
  in-memory and NATS JetStream adapters.
- Import domain messages only from `app.contracts`; never import another
  engine package.
- Preserve practical at-least-once semantics: explicit ack after success,
  negative ack on retryable handler failure, and idempotency by `event_id`.
- Wire payloads must pass strict `EventEnvelope` validation. Poison messages go
  to the DLQ before acknowledgement.
- Keep unit tests infrastructure-free. Tests needing a live NATS server must be
  marked `integration` and skipped unless `NATS_URL` is configured.
- Contract/API changes require root integrator review.
