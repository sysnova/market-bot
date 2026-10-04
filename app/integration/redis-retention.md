# Redis history retention

Normal analytical requests use shared, bounded Redis windows. Their capacities
are the largest requirement for each symbol/timeframe/session observed by the
current history service, rather than a maximum inherited from previous runs.
An oversized inherited window is discarded through reference-counted deletion
and reloaded from PostgreSQL. Concurrent consumers in the same service lifetime
keep the largest requested depth.

After central prewarm and before the history RPC server starts, historical windows
outside that preload are removed. Additional engine symbols are hydrated when
their engines request them. Pending intents and event snapshots are unaffected.

Forced requests (`force_refresh=True`), including opportunity and stream recovery,
ensure PostgreSQL coverage but do not warm shared Redis windows. The requester
uses the existing PostgreSQL loader and session/completed-bar filtering. These
one-off reads use temporary process memory while replay is running; their larger
retention is never installed in Redis. PostgreSQL historical bars are not deleted
by this policy.

This bounds historical cache growth for a fixed set of normal requirements;
it is not a fixed byte budget for all Redis data. More active symbols, larger
analytical requirements, and engine working views still increase memory use.
