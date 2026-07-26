# Alpaca market-data engine ownership

- This folder is read-only with respect to Alpaca: Stock Market Data endpoints
  only. Never add Trading API calls or order concepts here.
- Normalize provider records before publication and use the shared `MarketBar`
  contract for bars.
- Keep credentials out of envelopes, logs, exceptions and fixtures.
- Depend on other engines only through stable contracts or structural ports.
- Unit tests must use injected transports and must not access the network.
