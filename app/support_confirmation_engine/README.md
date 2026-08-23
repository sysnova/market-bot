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
`marketbot.v1.support-confirmation.transition.<STATE>.<SYMBOL>`. JetStream is the 7-day state and
replay boundary; the engine does not write text files or query the watchlist.

## Version 0.3

Version 0.3 keeps support discovery separate from confirmation:

- one nearby level is reported as `SINGLE_SUPPORT_NEARBY` and is never promoted into Swing;
- confluence requires independent source families and zones are ranked by quality plus proximity;
- assessments expose the actual zone sources, spot position, distance, touch count/freshness, and
  an actionability score;
- completed 1H bars are aggregated into 4H evidence so reclaim plus HH/HL can mature the thesis
  during the session;
- `BASE_BUILDING` and `LIQUIDITY_SWEEP` are pending states, while reversal remains below 60 until
  HH and HL form a paired structure on daily or 4H data.

MarketBot 7.30 keeps each Swing engine's native zone and invalidation authoritative. Support is
attached only after zone overlap and is labeled `CONTEXT`, `REACTION`, or `STRUCTURE`. Distance,
touch age, actionability, and B-wave risk can downgrade or reject the corroborating evidence; they
never create a Swing zone or promote native maturity by themselves.
