# Entry Recovery

Entry Recovery evaluates a new analytical entry after a tactical paper leg was invalidated while
another horizon remains open. It does not undo or rewrite the original invalidation. A recovery is
recorded as a separate `EntrySignal` with family `CORE_RECOVERY`, so its outcomes can be measured
independently from initial entries.

Version 1.0 requires a reclaimed stopped-leg entry, a still-open higher-horizon target, fresh bullish
Swing and Intraday evidence, a strong five-minute higher low, and the configured minimum reward/risk.
The engine never submits broker orders.

Version 1.1 preserves those rules but emits `EntrySetupAssessment` without L1-L4 maturity. Alert 3.2
owns the buy-quality decision and currently maps fresh Swing + Intraday recovery evidence to L2.
Version 1.0 remains available as the rollback implementation.
