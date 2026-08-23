# Scalp engine

`ScalpEngine` is a pure, deterministic same-session decision engine. It consumes the stable
`OrderFlowState` contract plus its own quote, VWAP, ATR and optional intraday-support context.
It deliberately accepts no LONG, Swing or broker state.

The v1 state machine is:

`WATCHING -> ARMED -> ENTRY_CONFIRMED -> MANAGING -> EXIT_CONFIRMED`

An armed setup can instead become `INVALIDATED`. Supported setup families are support reversal
and bullish VWAP reclaim. Entry is gated by spread, event and quote freshness, Order Flow
confidence, data quality and the ratio of unclassified volume. Management can confirm an exit at
the structural stop, target, maximum holding time or a fresh high-confidence bearish Order Flow
reversal.

The output is analytical evidence only. The engine has no clocks, I/O, persistence, quantities,
orders or Alpaca Trading API calls. A later integrator may persist its assessments and transitions
in the independent Intraday Opportunity lifecycle.
