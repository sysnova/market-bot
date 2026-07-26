# Persistence ownership

- This folder owns SQLAlchemy mappings, repositories, units of work, and their
  isolated tests and documentation.
- The versioned SQL migration is the DDL source of truth. Any mapping change
  must update the migration (or add a subsequent migration) and the DBML in the
  same change.
- Do not import another engine. Shared message types may come only from
  `app.contracts`; shared technical primitives may come from `app.common`.
- Generate UUIDv7 identifiers in Python and inject clocks/ID factories in unit
  tests.
- Keep transactions short and leave commit/rollback ownership to the unit of
  work. Use atomic upserts for checkpoints and health; use `SKIP LOCKED` for
  outbox workers.
- Never apply migrations to a remote Supabase project without root-integrator
  review and an explicit target check. Never put login credentials in SQL.
