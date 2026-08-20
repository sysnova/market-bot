# 4HGERI

Experimental shadow engine based on the horizontal-level sequence shown in the GERI reference.
It starts from a confirmed 4h pivot low. A completed close below active support confirms the
highest intervening high as the next resistance; a completed close above active resistance
confirms the lowest intervening low as the next support. Levels therefore alternate causally.

The third structural level is not maturity `L3`. When the active structural level is support,
entry observation remains independently `ARMED -> IN_ZONE_4H -> L2_4H -> L3/L4`.

Version `1.1.0` preserves the published N1/N2/N3 chain across new bars and process restarts. It
tracks the pending segment extreme explicitly and appends the next alternating level only after a
completed 4h close breaks the active level by its ATR buffer. Moving the rolling history window no
longer re-seeds or renumbers an active structure.

Version `1.2.0` retains `1.1.0` as a selectable legacy implementation and adds a standalone,
mirrored Swing reconstruction. It evaluates both causal seeds: support-first for `LONG` and
resistance-first for `SHORT`, then pins the first completed N1/N2/N3 structure. Its manual stages
are `G0 ARMED`, `G1 IN_ZONE`, `G2 FAST` (15-minute rejection), `G3 4H CONFIRMED`, and
`G4 CONTINUATION`, plus `EXTENDED`, `RECLAIM_REQUIRED`, and `INVALIDATED` guard states.

The `1.2.0` runtime is deliberately shadow-only. It consumes market history and live bars, publishes
only 4HGERI assessment/transition observations for its tmux monitor, and does not subscribe to daily
Swing or Entry Opportunities. It does not emit buy signals, create opportunities, or place orders.
MarketBot definition `7.19.0` selects this implementation; definition `7.18.0` remains pinned to
`1.1.0` for comparison.
