# Event bus

This engine owns the asynchronous transport boundary used by MarketBot. It
exposes a small `EventBus` protocol and two adapters:

- `InMemoryEventBus` for deterministic local development and unit tests.
- `NatsJetStreamEventBus` for durable inter-process delivery.

Both adapters accept only the frozen `app.contracts.EventEnvelope`. Subjects
passed to the API are relative to a configurable prefix (`marketbot` by
default). NATS `*` and `>` subscription wildcards are supported.

The in-memory adapter serializes a canonical snapshot at publish time and
decodes a fresh envelope for every delivery and replay. Nested mutable payloads
therefore cannot leak mutations from publishers or neighboring consumers.

## Delivery guarantee

Delivery is **at least once**, not exactly once. A successful handler return is
the acknowledgement boundary. Handler failures are retried, so consumers must
also persist/idempotently reject `event_id` values. Publishers use
`Nats-Msg-Id: <event_id>` to activate JetStream's duplicate window; this is a
transport optimization and not a replacement for consumer idempotency.

Invalid wire envelopes are published byte-for-byte to `<prefix>.dlq` with
diagnostic headers, then acknowledged so poison messages do not loop. The DLQ
is operational evidence and can contain untrusted bytes; consumers must never
deserialize it as a valid domain event without validation.

## Replay and durability

Set `SubscriptionOptions(replay_all=True)` to consume retained history. Give a
stable `durable_name` in production so JetStream resumes the same consumer.
When omitted, the NATS adapter creates a unique durable consumer for the
subscription lifetime. In-memory history is process-local and disappears on
restart.

`replay_latest_per_subject=True` uses a live `NEW` consumer followed by direct
last-message lookups for the matching exact subjects. It does not create a
JetStream `LAST_PER_SUBJECT` consumer: that operation scanned historical file
blocks and reproduced a multi-gigabyte server heap spike. The subject listing
contains metadata only; each matching subject contributes at most one envelope.
Live callbacks wait until restoration finishes, then retain explicit ack/nak
semantics. Overlap can produce duplicates, so handler idempotency remains required.
Restoration failures propagate through `wait_until_caught_up`; an incomplete
restore is never reported as caught up. Once restoration finishes or fails, live
delivery continues independently so one invalid snapshot cannot poison the durable
consumer. State consumers allow 64 unacknowledged live messages during restoration.
Existing named state subscriptions use a
`-current-v2` durable because JetStream delivery policies cannot be changed in
place; the adapter does not delete historical consumers or messages.

The integration root supplies Redis current-event storage when the shared Redis
cache is configured. A successful JetStream publication updates the current
snapshot, and delivery also captures events from other publishers. Snapshots
exclude raw bars/ticks and expire after seven days. For analysis results, Redis
atomically keeps the newest evaluation/data timestamp even if an older bootstrap
result is published later. Direct last-message reconciliation repairs a missing
Redis write after a publisher crash. Without Redis, the adapter restores the
last retained publication; it does not search older publications for a newer
analytical timestamp. An empty Redis cache has the same migration limitation
until current producers populate it; freshness is still evaluated from the
actual result timestamps. Administrative purges must include corresponding
Redis current-event keys when removing individual analytical records.

The `MARKETBOT` stream keeps all versioned events, including market bars, for
at most seven days. The limit is configured once on the stream; publications
do not carry per-message TTL metadata. Connecting the adapter migrates new and
existing streams to that retention policy. New streams disable per-message TTL;
NATS does not allow that capability to be disabled on a stream once enabled,
but legacy streams no longer receive messages that use it. A legacy stream with
`allow_msg_ttl=true` must be deleted and recreated during a maintenance window
to remove the irreversible capability and its in-memory TTL state. That
operation discards retained JetStream messages and consumer positions; it does
not affect PostgreSQL history or persisted Opportunities.

## Tests

Unit tests require no services. The integration contract requires a JetStream
server and `NATS_URL`, for example `nats://127.0.0.1:4222`:

```powershell
uv run pytest app/event_bus/tests
```
