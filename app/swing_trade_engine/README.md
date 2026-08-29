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
