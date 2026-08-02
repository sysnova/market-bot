# Elliott Wave shadow engine

Runs independently from Long, Swing, Intraday and PatreonCaps. Its universe is only active local
PostgreSQL holdings with positive quantity. It publishes observational `WaveAssessment` events to
`marketbot.v1.elliott-wave.assessment.<SYMBOL>` and never submits orders or changes another engine's
result.

The initial `0.1.0` hypothesis ranks daily Wave 2 and Wave 4 endings using confirmed pivots, Fibonacci
retracement bands, ATR-sized impulses, non-overlap validation and reversal follow-through. Ambiguous
or invalid structures remain explicitly `UNRESOLVED`.
