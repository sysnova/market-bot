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

The two root environments are platform-specific rather than engine-specific:

- native Windows uses `.venv-windows` through `scripts/windows/environment.ps1`;
- Linux and WSL use `.venv-linux` through the Linux launchers.

Never reuse or copy either environment across the Windows/WSL boundary. Recreate it from `uv.lock`.
In a Windows development shell, select the environment before running quality commands:

```powershell
$env:UV_PROJECT_ENVIRONMENT = Join-Path (Get-Location) ".venv-windows"
uv run ruff check .
uv run pyright
uv run pytest -m "not integration"
```

## Adding an engine

Create `app/<engine>/` with its implementation, tests, and a small README describing ownership and
public contract usage. Do not import another engine. If coordination needs a new message or port,
evolve `app/contracts/` explicitly and request root integrator review.

For a new implementation of an existing engine:

1. Keep the previous class for rollback and add the new class in the owning engine package.
2. If constructor options come from a rule artifact, extend that engine's `strategy.py`; never add
   its rule names or version branches to `MarketBotAssembly`.
3. Register the implementation version in `app/integration/engine_catalog.py`.
4. Add a new immutable strategy artifact and MarketBot definition; do not edit released YAML.

For a new logical engine, additionally add its `EngineSlot`, registry entry with an explicit
`required_since`, operational composition/command, runtime-process specification, and universe
policy. `required_since` protects every older immutable definition from becoming invalid. A new
composition can call `assembly.build(slot, dependencies...)` directly; a typed facade is optional
and should be added only when it improves an established public integration API.
