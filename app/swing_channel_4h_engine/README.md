# Swing Channel 4H

Shadow engine that derives an ascending support channel from two confirmed rising pivot lows and
a parallel line through the subsequent high. It consumes completed regular-session four-hour bars
and publishes its own maturity without replacing the daily `SWING` analysis.

Maturity:

- `ARMED`: a valid ascending channel exists.
- `IN_ZONE_4H`: live price is inside the ATR-padded projected support band.
- `L2_4H`: a later completed 4h bar confirms a higher-low bullish bounce.
- `L3`: L2 plus overlap with a favorable daily Swing entry zone.
- `L4`: L3 plus an existing core L3/L4 confirmation.
- `INVALIDATED`: price breaches projected support minus the invalidation buffer.
