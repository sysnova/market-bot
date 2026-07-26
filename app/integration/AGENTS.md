# Integration ownership

- This folder is owned by the root integrator.
- Concrete cross-engine imports are allowed only here and in repository composition roots.
- Keep external infrastructure behind `integration`-marked tests.
- Never move business rules into this package.
