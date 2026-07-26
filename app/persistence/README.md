# Persistence engine

This engine owns the PostgreSQL adapter for MarketBot. It maps the private
`market_bot` schema and provides short-lived async units of work for the inbox,
outbox, consumer checkpoints, and latest service-health snapshot.
It also stores fixed entry theses in `entry_watches` and their immutable audit history in
`entry_watch_transitions`.

## Runtime contract

- IDs are UUIDv7 values generated in Python; PostgreSQL has no UUID default.
- Runtime traffic uses SQLAlchemy 2 with async psycopg and a per-process pool of
  one base connection plus one overflow connection.
- Supabase deployments should use the session pooler URL on port 5432 and SSL.
  Local CI may explicitly disable SSL.
- Repository calls never commit. `PersistenceUnitOfWork` commits a successful
  context and rolls back failures, keeping transactions short.
- Delivery is at-least-once. `processed_events` deduplicates per consumer and
  event; `outbox_events` is claimed with `FOR UPDATE SKIP LOCKED`.

## Schema source of truth

Versioned files under `supabase/migrations/` are the DDL source of truth.
`resources/diagrams/market_bot.dbml` mirrors the complete schema. The migrations create no object
in `public` or `stock` and must be reviewed before being applied to the remote Stock project.

The group role `market_bot_runtime` is `NOLOGIN`, receives no `DELETE`, and is
the only role named in RLS policies. A separately managed login can be granted
membership at deployment time; credentials never belong in migrations.

## Verification

```powershell
$env:UV_CACHE_DIR = Join-Path $env:TEMP 'market-bot-uv-cache'
uv run pytest app/persistence/tests
uv run ruff check app/persistence
uv run pyright app/persistence
```
