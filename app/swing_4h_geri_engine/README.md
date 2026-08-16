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
