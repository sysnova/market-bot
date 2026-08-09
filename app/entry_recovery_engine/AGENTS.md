# Entry Recovery Engine

- Own recovery decisions after a paper-entry leg was invalidated while a higher-horizon thesis remains active.
- Consume only stable contracts; never import Entry Watcher or Entry Opportunity implementations.
- Emit `EntrySignal` values with family `CORE_RECOVERY`; never create orders, sizing, or broker instructions.
- Keep recovery rules independently versioned from initial-entry confirmation rules.
- Require fresh evidence, a reclaimed level, and explicit reward/risk before confirming recovery.
