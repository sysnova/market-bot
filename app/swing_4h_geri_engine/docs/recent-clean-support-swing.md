# 4HGERI 1.13.0: recent valid structure and optional Fibonacci

Assembly 7.57.0 retains all other selections from 7.56.0. Prior versions remain
available for rollback. The regular-session bar construction is unchanged.

Each causally confirmed clean local support in the last 60 bars becomes a
candidate: the configured pivot radius requires correction into the source low,
no lower neighboring lows, and subsequent recovery above the source candle high.
Each candidate follows the existing first-perforation N3 rule independently.
N2 is the maximum between N1 and N3, inclusive. N3 never moves lower: a subsequent
perforation invalidates that particular pattern. A later 4H close above N2 confirms.

Selection excludes invalid patterns, then prioritizes complete N1/N2/N3 patterns
by N3 source date. Equal N3 dates prefer the newer N1 source. If there is no valid
complete pattern, the latest clean unbroken N1 is shown as BUILDING without a
zone. No Fibonacci value, score, distance to current price, or confirmation
strength participates in this ranking. A saved selected candidate can persist
beyond the rolling input window; it competes with newly formed candidates instead
of blocking them. This does not impose an unvalidated fixed-age expiration.

Fibonacci is context only. At N3 formation, find the highest high in the preceding
input history and the lowest low strictly before that high. These independent,
chronological extrema define the dominant reference impulse. The 38.2%-61.8%
retracement band is a documented convention, not a prerequisite or empirically
calibrated threshold. Confluence is true when N3 lies inside that band; mere overlap
of the broad N3-N2 range is insufficient. Missing or noncoincident Fibonacci does
not change selection, maturity, confirmation flags, or eligibility. The anchors,
dates, retracement and band are published in structural_fibonacci_* metrics and
freeze for a persisted identical N1/N3. No opportunity or order is created by this
annotation. A fresh calculation uses only its supplied history; wider history may
produce different contextual anchors, which are always disclosed.

Regression data: tests/fixtures/uuuu_20260910.json contains the 60 real Alpaca SIP
bars used in the prior audit, aggregated from 15-minute data with the repository's
RegularSessionFourHourAggregator. No network access is needed by the tests. At
2026-09-10 close the new engine selects N1=13.6100 (Aug 20 PM), N2=16.5000
(Aug 26 AM), N3=13.6050 (Sep 10 PM), IN_ZONE_4H without N2 break confirmation.
