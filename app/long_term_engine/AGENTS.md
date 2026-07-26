# Long-term engine ownership

- This folder owns deterministic long-horizon technical analysis, its tests, fixtures, and docs.
- Accept normalized historical bars; never fetch market data or import another engine.
- Keep calculations pure: no network, database, clock, randomness, or process-global caches.
- Emit analysis, levels, scores, reasons, and risk flags; never place or recommend an order.
- Add a failing unit test before implementation and keep fixture expectations stable.
