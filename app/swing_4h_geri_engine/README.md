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
MarketBot definition `7.20.0` retains this implementation for comparison.

Version `1.3.0` preserves the complete structural `1.2.0` chain and adds a separate tactical
countertrend lane. While the pinned structure remains `SHORT`, the lane may track a confirmed local
pivot low as a tactical `LONG`; the mirrored rule applies above a structural `LONG`. Its entry band
is the pivot plus/minus `0.25 ATR14`, invalidation is `0.50 ATR14` beyond the pivot, maximum extension
is `1.50 ATR14`, minimum reward/risk is strictly greater than `1.50`, and tracking expires after five
sessions. The structural active level is the tactical target. MarketBot `7.21.0` keeps this lane in
manual assessment/tmux mode. Definition `7.22.0` projects eligible tactical `LONG` states as
`GERI_COUNTERTREND` signals into Entry Opportunity `5.0.0`: `CT0` watches and `CT1-CT4` paper-track
the rebound and its P/L. Tactical `SHORT` remains analytical because Opportunities are long-only.
Neither version places orders; `7.20.0` stays pinned to `1.2.0` for comparison.
