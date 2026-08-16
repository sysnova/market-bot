# 4HGERI engine ownership

- This folder owns only causal horizontal-level reconstruction and pure maturity rules.
- Do not import integration, transport, persistence, broker, or another engine.
- Every level must be reproducible from chronological completed 4Hour bars without lookahead.
- Structural level numbers are not core Entry Opportunity maturity levels.
