# Entry Opportunity Engine

- Own the durable paper-trade lifecycle after Entry Watcher emits a transition.
- Consume contracts only; never import another engine implementation.
- Preserve the original thesis and advance one active opportunity per ticker.
- Keep persistence behind `EntryOpportunityStore` and lifecycle events append-only.
- This engine audits simulated entries; it must never place broker orders.
