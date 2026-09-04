# SwingTrade 1.0

Independent, watchlist-only LONG swing engine selected by MarketBot `7.20.0`.
MarketBot `7.19.0` remains immutable and does not contain this slot.

The engine uses completed daily bars for a parameterized Fibonacci impulse
(60 sessions initially), support/resistance over 20 sessions and ATR14 risk.
It evaluates only completed 15-minute bars and publishes hierarchical maturity:

`analyze_geometry()` is the non-operational visualization path. When the absolute
60-session high precedes the absolute low, it selects the widest positive causal
low-to-later-high pair. It uses the same configured ratios, ATR, support window,
invalidation and targets without changing the versioned operational thesis rule.

- `ST1`: valid impulse, distance and strict primary R/R greater than 1.5.
- `ST2`: 20-session support band intersects the Fibonacci entry zone.
- `ST3`: spot is inside the Fibonacci zone and opens the paper Swing leg.
- `ST4`: spot is also inside a fresh, valid standalone 4HGERI LONG support zone.

ST maturity is stored separately from Core `L1-L4`. The runtime creates or
updates Opportunities and highlighted ST3/ST4 operator notices, but never sends
orders to a broker.

The dedicated operator dashboard is available with
`marketbot monitor swing-trade`. The Linux launcher creates the `SwingTrade`
tmux window automatically and displays the latest full assessment for every
Watchlist symbol, including impulse dates, Fibonacci levels, 20-day movement,
invalidation, targets, R/R, GERI confluence, eligibility and rejection reasons.

Configuration lives in `configs/rules/swing_trade/1.0.0.yaml`; changing the
Fibonacci lookback to 70 or 80 sessions requires only a new immutable rule
artifact/definition, not an engine code change.

Candidate version `1.1.0` separates location from entry confirmation. A price inside Fibonacci
remains at most `ST2` until two completed 15-minute bars confirm a rejection, the close passes the
bar VWAP and volume exceeds a same-clock-slot baseline. `ST4` additionally requires a GERI
`G2/G3/G4` reaction rather than an armed support zone alone. The engine rejects bars, quotes or
GERI evidence later than `as_of`. Rules are versioned in `configs/rules/swing_trade/1.1.0.yaml`.

`ST3` and `ST4` are projected into the confirmed-buy monitor with their native ST maturity. A
later demotion resets the notification latch, so a genuinely new rejection may confirm again while
unchanged repeated assessments remain silent. The isolated backtest accepts `--source-end-date`
to preserve the opportunity and its target/invalidation lifecycle across multiple sessions.

At runtime bootstrap, the latest completed RTH 15-minute bar may rebuild analytical state, but it
may open a new actionable `ST3`/`ST4` signal only for 30 minutes after that bar completed. Older
bootstrap evidence still publishes assessment and transition state so demotions and thesis closure
remain reconcilable, without presenting a previous-session rejection as a new confirmed entry.

## Recovery quality observation (1.6.0)

MarketBot `7.49.0` selects implementation `1.6.0` and strategy `1.3.0`. It inherits
the native `1.4.0` decision, including Fibonacci, rejection/VWAP/RVOL, GERI, risk and
targets. Order-flow enrichment from `1.5.0` is not enabled. `7.48.0` remains available
for rollback. This release measures recovery quality; it does not strengthen ST3
entry requirements, promote maturity, delay entries or add a bearish Core veto.

The assessment and `marketbot monitor swing-trade` now show:

- `WATCHING`: the native engine has not reached ST3/ST4.
- `EARLY_REACTION`: native ST3/ST4 without the observed local breakout.
- `LOCAL_BREAKOUT`: the latest 15-minute close exceeds the highs of the preceding
  two completed, consecutive bars in the same session.
- `RECOVERY_WITH_MOMENTUM`: that breakout also has an improving 4H MACD histogram.

These are evidence labels, not calibrated probabilities or a win-rate ranking.
An unavailable local reference is recorded as null, distinct from a failed breakout.
The daily MACD is independent context and never blocks a recovery below zero.
Likewise, a negative 4H histogram may be improving. The metrics preserve line,
signal, current/previous histogram, direction, availability, sample count and the
closing time of the last usable observation. MACD uses Decimal EMA(12,26,9), SMA
seeds and at least 35 completed bars to compare two histograms. Future and unfinished
bars are excluded. Data older than 96 hours is marked STALE with unknown direction.

Runtime 1.6.0 requests 60 calendar days/up to 1200 RTH 15-minute bars for warm-up;
the native confirmation window stays at 160 bars. The 4H convention matches the
existing RTH channel aggregator: 09:30–13:30 ET and 13:30–16:00 ET (the second is
2.5 hours). Both require all their constituent 15-minute bars. MACD daily history
starts from the supplied daily bars and rolls forward only after both RTH segments
are complete, in a separate observation history so native geometry is unaffected.
The fixed RTH convention does not synthesize missing bars or early-close sessions;
missing history is visible and does not become a bearish vote. A restart needs
history sufficient to rebuild the completed segments.

Every subsequent 15-minute assessment retains the observations for outcome analysis.
When native decision/geometry remain unchanged, runtime publishes only an assessment,
without a duplicate transition or EntrySignal. Confirmed-buy events retain their
native ST3/ST4 maturity and include the observed quality in their reasons. No broker
orders are placed. Historical performance comparisons must use the observations
available at entry, not later quality updates, and still require out-of-sample validation.
