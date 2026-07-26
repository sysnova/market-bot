# Architecture

MarketBot is a modular monorepo organized around independently evolving engines. Each engine owns
its code, tests, and local documentation under `app/<engine>/`.

## Boundaries

```text
operator / external input
          |
          v
   stable contracts  <---->  engine A
          ^                   (private model)
          |
          +--------------->  engine B
                              (private model)
```

An engine never imports another engine. Cross-engine communication uses stable types and ports from
`app/contracts/`, normally transported over NATS JetStream. PostgreSQL is durable infrastructure;
schema ownership must remain explicit rather than becoming an implicit shared model.

`app/common/` contains technical primitives only: clock injection, IDs, canonical encoding,
configuration, and logging. It must not become a dumping ground for shared business behavior.

## Determinism

Time and entropy are injected at boundaries. Persisted or signed payloads use canonical JSON and a
SHA-256 content digest. Identifiers are UUIDv7 so their leading 48 bits preserve millisecond creation
order while retaining RFC-compatible randomness.

## Configuration and observability

Runtime settings use Pydantic and the `MARKETBOT_` environment namespace. Secrets use `SecretStr`
and diagnostic representations are redacted. Structlog emits JSON by default and supports bound
correlation context; local development may opt into human-readable output.
