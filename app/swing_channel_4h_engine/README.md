# Swing Channel 4H

Shadow engine that derives an ascending support channel from two confirmed rising pivot lows and
a parallel line through the subsequent high. It consumes completed regular-session four-hour bars
and publishes its own maturity without replacing the daily `SWING` analysis.

Version `1.1.0` pins the selected A/B/C geometry when the channel becomes active. New completed
bars project the same slope, width, support zone, and invalidation distance instead of selecting a
different pivot pair from the rolling history. A replacement geometry may be selected only after
the pinned channel is genuinely `INVALIDATED`; candidate replacements must also pass minimum
containment and ATR-width quality gates.

Version `1.2.0` only admits A/B support pivots separated by at least eight completed 4H bars
(four RTH sessions). Between A and B there must be a material ATR-normalized impulse followed by
a pullback into the confirmed higher low B, and price cannot pierce the candidate support line by
more than its invalidation buffer. This prevents two nearby local lows from defining a long-lived
projected channel. Active geometry from an older implementation is re-evaluated before it can be
pinned under this stricter rule.

Maturity:

- `ARMED`: a valid ascending channel exists.
- `IN_ZONE_4H`: live price is inside the ATR-padded projected support band.
- `L2_4H`: a later completed 4h bar confirms a higher-low bullish bounce.
- `L3`: L2 plus overlap with a favorable daily Swing entry zone.
- `L4`: L3 plus an existing core L3/L4 confirmation.
- `INVALIDATED`: price breaches projected support minus the invalidation buffer.
