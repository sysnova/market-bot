# MarketBot repository guide

## Structure and ownership

- Every independently evolving engine lives in `app/<engine>/`.
- An engine owns its implementation, tests, fixtures, and focused documentation inside that folder.
- Shared technical primitives may live in `app/common/`; business rules may not.
- Stable cross-engine messages and ports live in `app/contracts/` and require compatibility review.
- Repository-wide configuration, dependency resolution, CI, and integration policy are owned at the root.
- There is one root `pyproject.toml` and one root `uv.lock`. Engines must not create nested environments or dependency manifests.

## Dependency boundaries

- Engines must not import another engine's package.
- Collaboration occurs only through `app/contracts/` and external adapters such as NATS or PostgreSQL.
- `app/common/` must not import from any engine.
- The root is the unique integrator: it composes engines, owns cross-engine checks, and resolves shared dependency versions.
- Do not hide a circular dependency behind dynamic imports. Extract an explicit contract instead.

## Development policy

- Target Python `>=3.14,<3.15` and use `uv` for environments, locking, and commands.
- Add a failing test before implementation. Keep unit tests isolated from network, databases, clocks, and entropy through injection.
- Put engine-specific tests and docs under that engine's folder. Root `docs/` is reserved for repository-wide architecture and operations.
- Mark tests that need services with `@pytest.mark.integration`; ordinary unit tests must need no secrets or running containers.
- Before handoff run `uv run ruff check .`, `uv run pyright`, and `uv run pytest`.
- Never commit credentials. Configuration exposed in diagnostics must redact `SecretStr` values.

## Runtime data sources

- Do not query Supabase, its dashboard, Data API, MCP server, or remote projects for this repository unless the user explicitly requests Supabase.
- Runtime persistence is local PostgreSQL configured by `MARKETBOT_DATABASE_URL` and local NATS JetStream configured by `MARKETBOT_NATS_URL`/`MARKETBOT_*` settings.
- Treat files under `supabase/migrations/` as versioned PostgreSQL schema artifacts; their presence does not authorize or imply a Supabase lookup.
- For persisted analytical events, inspect the local `MARKETBOT` JetStream and filter `marketbot.v1.analysis.result.>`.

## Change coordination

- Respect folder ownership during parallel work and do not revert unrelated edits.
- Changes to shared contracts, root dependencies, or CI need root integrator review.
- Prefer additive, backwards-compatible contract evolution. Record irreversible architecture decisions before implementation.
