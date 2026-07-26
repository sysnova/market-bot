# Swing engine

Deterministic swing analysis over completed daily bars and one intraday confirmation series
(`15Min` or `1Hour`). The engine looks for two constructive states: a controlled pullback in
an established uptrend, or an early breakout through 20-day resistance with confirming
volume. Broken daily structure is classified as avoid rather than converted into an order.

The calculation includes SMA 20/50, RSI(14), ATR(14), daily and intraday relative volume,
20-day resistance, 10-day support, a technical invalidation level, risk percentage, risk in
ATR units, and a two-R target used only to assess asymmetry. Invalidation must remain below
the observed price and within both an 8% and 3-ATR risk budget for a favorable verdict.

`SwingEngine.analyze()` returns the shared `AnalysisResult` contract with `horizon=SWING`.
`evaluate()` returns the engine-owned detailed calculation. Both methods are pure; Alpaca,
NATS, persistence, alert presentation, and order execution belong to external adapters.

Run the focused suite with:

```powershell
uv run pytest app/swing_engine/tests
```
