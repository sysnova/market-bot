# Clean support swing — 4HGERI 1.12.0

N1 is the low (including wick) of a confirmed local support. The configured
pivot radius requires lows at or above N1 on both sides, a strictly higher low
on its left, and growth above the source candle high on its right. Equal touches
are allowed. The first subsequent candle whose low is strictly below N1 fixes
N3 at that candle's low, even when its close recovers N1.

N2 is the highest high from the N1 source candle through the N3 candle,
inclusive. N2 and N3 become available together on that first crossing; N2 need
not be broken yet. The structural zone is exactly [N3, N2]. A later completed
4H close strictly above N2 confirms the swing (G3). A quote or wick above N2,
or a close equal to N2, does not. No ATR margin is added to the structural
break. The existing independent recovery/SHORT lanes retain their own rules.

N3 is fixed. A later low strictly below N3 invalidates the structure, taking
precedence over a simultaneous N2 close break. A new structure requires a new
clean support originating at or after invalidation. State persists across rolling
windows, including the running maximum before the N1 break. Historical replay
uses the same chronological transitions, without selecting a later lower N3.

The exact floor is published as invalidation. Confirmation timestamps identify
completed evidence bars using the existing bar timestamp convention. These are
structural monitoring states, not order instructions.

Assembly 7.56.0 selects 1.12.0 and preserves the other selections from the
workspace's prior default 7.53.0. Older implementations and definitions remain
available for rollback. Existing runtime overrides of MARKETBOT_DEFINITION_PATH
must select the new definition to use it; changing source does not restart WSL.
