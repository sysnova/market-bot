# Long portfolio engine ownership

- Own deterministic, long-horizon portfolio-entry policy and its tests.
- Consume only shared `AnalysisResult` values; never fetch data or submit orders.
- Keep portfolio capital, allocations, exclusions, thresholds, and tranche sizing in an exact-version YAML artifact.
- Never depend on Swing or Intraday horizons for a LONG portfolio decision.
- Use `Decimal` for money and percentages and explicit UTC clocks supplied by callers.
