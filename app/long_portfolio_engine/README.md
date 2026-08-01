# Long Portfolio Engine v1

This engine monitors only the equity allocations in the Captain model portfolio for
accumulation through 2026-12-31. It consumes `LONG_TERM` analysis results and has no
dependency on Swing or Intraday signals.

An alert requires a favorable bullish `buy_zone` result on two distinct sessions,
minimum setup/entry/trend scores, an allowed market regime, and no blocked weekly risk
flag. Suggested purchases are capped at the gap between the target allocation and the
current `stock.customer_holding` market value; no buy is emitted once the target is
reached. It is analysis-only and never submits an order.

Rule thresholds and capital are frozen in `configs/rules/long_portfolio/1.0.0.yaml`.
Equity membership and target weights come from active local PostgreSQL watchlist rows
tagged `PORT_YTD`; retain rule artifacts for rollback and historical comparison.

Run it with:

```powershell
uv run marketbot engine long-portfolio
```

The distributed Windows launcher starts it automatically. Alerts are deduplicated in
`.runtime/alerts/long-portfolio-alerts-YYYY-MM-DD.ndjson` and published to the normal
local-alert NATS subject for the confirmed-buy monitor.

Each alert is inserted first into the immutable local PostgreSQL table
`market_bot.long_portfolio_alerts`; duplicate keys are ignored atomically. The launcher
also opens the dedicated WSL tmux session `marketbot-long`. Reattach manually with:

```powershell
wsl tmux attach-session -t marketbot-long
```

`SILVER`, `ETH`, and `PALLADIUM` remain reserved allocations but are deliberately not
evaluated through Alpaca's stock feed. The seven explicitly excluded symbols are folded
into cash rather than redistributed.
