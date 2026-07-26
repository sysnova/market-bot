# Synthetic Core ownership

This folder owns the trusted synthetic rules, strict parameter models, provider
factories, manifests, tests, goldens, and focused documentation.

- Keep every rule synchronous, deterministic, and free of I/O.
- Add a failing test and update goldens intentionally before changing behavior.
- Preserve exact semantic versions; never silently replace a registered version.
- Never discover code from strategy YAML or evaluate binding strings here.
- Do not import registry, runtime, event bus, persistence, or another engine.
- Use only `app/contracts` and technical primitives from `app/common`.
- Treat the timeout rule as a process-termination fixture, not normal application code.
- Rule subprocesses are not a security sandbox; providers must remain trusted code.
