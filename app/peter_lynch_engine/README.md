# Peter Lynch engine

Manual, read-only fundamental screen for the active local watchlist. Run it with:

```powershell
uv run marketbot engine peter-lynch
```

It requires the existing Alpaca market-data credentials and a contact-bearing
`MARKETBOT_SEC_USER_AGENT`, for example `MarketBot/0.1 operator@example.com`. The SEC
identity is not an API key.

The screen uses six strict financial gates: trailing P/E, projected forward P/E,
debt/equity, three-year diluted-EPS CAGR, PEG, and market capitalization. An open-market
Form 4 purchase in the prior 365 days is retained as an informational signal and does
not affect eligibility. The projected forward P/E is a historical extrapolation, not
analyst consensus.

Successful candidates receive the `LYNCH` watchlist indicator. Every valid evaluation
replaces `indicatorDetails.LYNCH`; transient provider failures leave existing metadata
unchanged. The command runs once and exits and is intentionally absent from both startup
launchers.

Before calling Alpaca or SEC, the process reads the persisted `indicatorDetails.LYNCH`
metadata. A symbol is skipped while its analysis is inside
`MARKETBOT_PETER_LYNCH_ANALYSIS_TTL_DAYS` (90 UTC days by default) and both its engine
and policy versions match the selected implementation. Expiration or either version
changing makes the symbol pending again. This applies to eligible and rejected results;
transient provider failures continue to preserve the previous metadata.

One manual run can override the configured window without changing the environment:

```powershell
uv run marketbot engine peter-lynch --ttl-days 30
```
