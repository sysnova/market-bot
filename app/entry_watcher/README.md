# Entry Watcher

The Entry Watcher preserves a Long entry thesis across later market evaluations. It freezes
the original buy zone, invalidation, expected correction, source analysis, and expiry instead
of recalculating those levels away when price finally pulls back.

An opportunity starts as `ARMED`, moves to `IN_ZONE` when price reaches the original area,
and becomes `TRIGGERED` when fresh Long, Swing, and Intraday analyses agree. SEC dilution evidence
is attached as a warning and never gates or invalidates an entry. Explicit Long structural failure
or a breach of the original invalidation changes it to `INVALIDATED`; elapsed time changes it to
`EXPIRED`.

The engine is analysis-only. It emits transitions for human alerts and has no order port.
