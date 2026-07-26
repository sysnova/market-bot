# Development

## Test-driven workflow

1. Add a focused failing test inside the owning engine.
2. Implement the smallest behavior that satisfies the contract.
3. Refactor while preserving engine boundaries.
4. Run Ruff, Pyright, and the unit suite before handoff.

Unit tests must not require credentials, Docker, wall-clock sleeps, or network access. Inject a
`Clock` and entropy provider when behavior depends on time or identifiers. Mark service-dependent
tests with `@pytest.mark.integration`.

## Dependency changes

All Python dependencies belong in the root `pyproject.toml`. After a change, run `uv lock` and commit
the root `uv.lock` with the manifest. Do not add per-engine manifests or virtual environments.

## Adding an engine

Create `app/<engine>/` with its implementation, tests, and a small README describing ownership and
public contract usage. Do not import another engine. If coordination needs a new message or port,
evolve `app/contracts/` explicitly and request root integrator review.
