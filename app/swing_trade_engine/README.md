# SwingTrade 1.0

Independent, watchlist-only LONG swing engine selected by MarketBot `7.20.0`.
MarketBot `7.19.0` remains immutable and does not contain this slot.

The engine uses completed daily bars for a parameterized Fibonacci impulse
(60 sessions initially), support/resistance over 20 sessions and ATR14 risk.
It evaluates only completed 15-minute bars and publishes hierarchical maturity:

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
