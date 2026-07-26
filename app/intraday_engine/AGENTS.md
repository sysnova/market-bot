# Intraday engine ownership

- This folder owns all Intraday v1 rules, fixtures, tests and documentation.
- Accept only normalized `MarketBar` history supplied by the integrator.
- Keep evaluation pure, deterministic, Decimal-based and free of I/O or clocks.
- Output analytical setups and levels only. Never add orders, quantities,
  brokerage calls or position execution state.
- Do not import another engine. Cross-engine collaboration uses contracts.
