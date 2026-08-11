# Volume Structure

Detects regular bullish divergence between confirmed weekly price lows and weekly
On-Balance Volume lows. A result describes possible accumulation or absorption; it
does not identify buyer type and cannot express an order.

The engine emits `AnalysisResult` on the independent `VOLUME_STRUCTURE` horizon.
`DEVELOPING` contributes only prioritization, `DIVERGENCE_CONFIRMED` contributes a
bounded +6 evidence boost, and `RECLAIM_CONFIRMED` contributes at most +10. Price
execution, risk/reward, dilution, and entry-confirmation gates remain independent.

Version 1.1 freezes invalidation from the ATR available at the second price pivot. If
any later completed weekly bar closes at or below that level, the result changes to
`DIVERGENCE_INVALIDATED`, becomes neutral, and contributes no evidence boost. A later
reclaim does not revive the historical divergence; a new confirmed pivot pair is required.
