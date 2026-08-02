# Signal Fusion shadow engine

`signal-fusion@0.2.0` consumes stable NATS contracts from Long, Swing, Intraday, SEC dilution,
Support Confirmation, Elliott Wave, and PatreonCaps. It never imports another engine or recalculates
their indicators. PatreonCaps is displayed as derived context and does not add an independent vote.

The terminal view separates support progress into `Z` (a valid live support zone), `R` (a reaction
or defense score of at least 60), and `S` (confirmed bullish structure). Only `S` is the hard support
gate for `ARMED` and `BUY_CONFIRMED`; `Z:Y R:Y S:N` means support held but reversal is not confirmed.

`BUY_CONFIRMED` remains SHADOW and requires every deterministic structure, trend, timing,
execution, dilution, portfolio, and reward/risk gate. The engine does not submit broker orders.
