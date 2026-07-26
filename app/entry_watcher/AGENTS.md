# Entry Watcher ownership

- This engine owns the durable entry-thesis state machine, its models, ports, tests, and docs.
- Consume only stable `app.contracts` analysis messages; never import another engine.
- Freeze the original target zone and invalidation when a thesis is armed.
- A later analysis may confirm, invalidate, or expire a thesis, but must not silently rewrite it.
- Keep persistence behind the store port and inject all clocks and identifiers in tests.
- This engine emits analytical transitions for human alerts only; it never creates orders.
