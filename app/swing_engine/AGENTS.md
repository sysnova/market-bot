# Swing engine ownership

- This folder owns deterministic swing-horizon technical analysis, tests, fixtures, and docs.
- Consume only normalized completed `MarketBar` values supplied by the composition layer.
- Never fetch data or depend on a clock, database, transport, credentials, or another engine.
- Emit `AnalysisResult(horizon=SWING)` plus optional engine-owned detail; never create orders.
- Keep ATR, levels, invalidation, and scoring reproducible with `Decimal` arithmetic.
- Add a failing unit test before implementation.
