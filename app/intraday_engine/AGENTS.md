# Intraday engine ownership

- This folder owns all versioned Intraday rules, fixtures, tests and documentation.
- Preserve previous engine classes when introducing a new active version; composition must
  select the desired version explicitly.
- Accept only normalized `MarketBar` history supplied by the integrator.
- Keep evaluation pure, deterministic, Decimal-based and free of I/O or clocks.
- Output analytical setups and levels only. Never add orders, quantities,
  brokerage calls or position execution state.
- Do not import another engine. Cross-engine collaboration uses contracts.
