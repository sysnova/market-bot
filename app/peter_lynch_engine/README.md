# Peter Lynch engine

Manual, read-only fundamental screen for the active local watchlist. Run it with:

```powershell
uv run marketbot engine peter-lynch
```

It requires the existing Alpaca market-data credentials and a contact-bearing
`MARKETBOT_SEC_USER_AGENT`, for example `MarketBot/0.1 operator@example.com`. The SEC
identity is not an API key.

The screen uses seven strict gates: trailing P/E, projected forward P/E, debt/equity,
three-year diluted-EPS CAGR, PEG, market capitalization, and an open-market Form 4
purchase in the prior 365 days. The projected forward P/E is a historical extrapolation,
not analyst consensus.

Successful candidates receive the `LYNCH` watchlist indicator. Every valid evaluation
replaces `indicatorDetails.LYNCH`; transient provider failures leave existing metadata
unchanged. The command runs once and exits and is intentionally absent from both startup
launchers.
