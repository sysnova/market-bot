# Support Confirmation analysis engine

Separates a locally valid support reaction from evidence of a new bullish trend. Version `0.2.0`
classifies V recoveries, base construction/breakout and liquidity sweep/reclaim patterns on key
higher-timeframe support. It publishes observations only, never orders, and initially runs only for
active positive holdings.

`NO_NEARBY_SUPPORT` means no actionable confluence is close enough to current price. It does not
erase the structural map: assessments retain the nearest higher-timeframe references, the weekly
SMA200 when available, and the latest confirmed pivot that launched a >=15% and >=4 ATR impulse.

PatreonCaps and ElliottWave remain independent. A future Signal Fusion consumer may compare their
events after out-of-sample validation.

The runtime publishes the latest assessment at
`marketbot.v1.support-confirmation.assessment.<SYMBOL>` and append-only state changes at
`marketbot.v1.support-confirmation.transition.<STATE>.<SYMBOL>`. JetStream is the 15-day state and
replay boundary; the engine does not write text files or query the watchlist.
