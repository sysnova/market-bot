# Support Confirmation shadow engine

Separates a locally valid support reaction from evidence of a new bullish trend. Version `0.1.0`
classifies V recoveries, base construction/breakout and liquidity sweep/reclaim patterns on key
higher-timeframe support. It publishes observations only, never orders, and initially runs only for
active positive holdings.

PatreonCaps and ElliottWave remain independent. A future Signal Fusion consumer may compare their
events after out-of-sample validation.

The runtime publishes the latest assessment at
`marketbot.v1.support-confirmation.assessment.<SYMBOL>` and append-only state changes at
`marketbot.v1.support-confirmation.transition.<STATE>.<SYMBOL>`. JetStream is the 15-day state and
replay boundary; the engine does not write text files or query the watchlist.
