# Order Flow and operational intraday paper scalping

MarketBot 7.32 adds an analytical microstructure lane without granting execution authority to
the market-data or analysis processes.

## Runtime flow

1. The existing Alpaca SIP WebSocket remains the only provider connection. Bars cover the normal
   market universe; trades and quotes cover a bounded universe that prioritizes positive holdings
   and then the active watchlist.
2. `order-flow` consumes typed quotes, trades, corrections and cancellations from Core NATS. It
   publishes states at most once per second per symbol, plus immediate material transitions, under
   `marketbot.v1.order-flow.*`.
3. `scalp` joins fresh Order Flow with completed one-minute bars, session VWAP and the latest
   Support Confirmation zone. It publishes only analytical maturity under
   `marketbot.v1.scalp.*`; it never imports a LONG or Swing thesis. Version 1 recognizes LONG
   reversals/reclaims and SHORT VWAP rejections, and can rearm after a completed round trip.
4. `intraday-opportunity` consumes confirmed scalp maturity and tracks conservative paper fills,
   P/L, MFE/MAE, stop, target, maximum hold and end-of-day closure. It never calls Alpaca Trading.
5. When price is inside a current Support Confirmation zone, Order Flow emits a short-lived
   support assessment. Swing 13.0, 4HGERI 1.7 and SwingTrade 1.5 consume it only when that Support
   zone overlaps their own native geometry. It adds evidence and provenance but cannot change
   verdict, score, maturity, eligibility, levels or invalidation.

The prior `portfolio-flow` engine remains compatible with typed ticks during migration, but new
microstructure decisions must use `OrderFlowState` so classification is not duplicated.

## Configuration

- `MARKETBOT_DEFINITION_PATH=configs/marketbot/7.32.0.yaml`
- `MARKETBOT_MICROSTRUCTURE_MAX_SYMBOLS=40` limits live trade/quote load.
- `MARKETBOT_INTRADAY_PAPER_NOTIONAL=1000` sets the simulated notional per confirmed scalp.
- `MARKETBOT_ALPACA_DATA_FEED=sip` is recommended; IEX does not represent the consolidated tape.
- `MARKETBOT_ALPACA_EXECUTION_ENABLED` remains hard-disabled by its settings type.

Apply `supabase/migrations/20260823233000_intraday_opportunity_lifecycle.sql` to the local
PostgreSQL database before starting the distributed runtime. Despite the directory name, the
migration is a local versioned PostgreSQL artifact and must not be applied to a remote project by
automation.

The Linux/WSL launcher synchronizes `.venv-linux`, selects `7.32.0` unless an explicit
`MARKETBOT_DEFINITION_PATH` rollback is exported, and applies this migration when all three owned
relations are absent. It refuses non-local databases and partial schemas, and restarts an existing
tmux runtime when its readiness files belong to an older MarketBot definition.

The operational process emits `intraday-opportunity.transitioned` for every paper open, mark and
close and persists the current snapshot, entry/exit fills and append-only events. Inspect the last
week with:

```powershell
uv run python -m app.operator_cli intraday-opportunity report --days 7
```

The report separates open trades from closed outcomes and exposes effectiveness, expectancy,
profit factor, gross/net P/L and average MFE/MAE.

The Linux/WSL launcher also creates two independent tmux windows when the corresponding engines
are active:

- `Scalping` combines the latest Order Flow windows with Scalp maturity, direction, entry, stop,
  target, VWAP, spread and reasons. Run it directly with
  `uv run marketbot monitor scalping`.
- `IntradayOps` combines event-driven paper marks with PostgreSQL recovery, live net P/L,
  MFE/MAE, close reasons and the rolling seven-day effectiveness summary. Run it directly with
  `uv run marketbot monitor intraday-opportunity`.

Neither monitor can place broker orders, and neither mixes its lifecycle with Swing Entry
Opportunities.

## Historical replay and walk-forward

`AlpacaRestClient.fetch_trades()` and `fetch_quotes()` provide paginated historical data.
`HistoricalOrderFlowReplay` merges both streams causally and orders a quote before a trade when
their exchange timestamps are identical. Walk-forward calibration must split by complete market
sessions and evaluate out-of-sample sessions with frozen thresholds. Fills must use ask for a LONG
entry, bid for a SHORT entry, and the opposite liquidating side for marks and exits.

Minimum activation evidence includes net expectancy after spread/fees, profit factor, maximum
drawdown, MFE/MAE, hold time, quote age, unknown-classification ratio and end-to-end p50/p95/p99
latency. Shadow and paper results must remain identifiable by engine and strategy version.

## Safety boundary

This release is operational in paper mode: it emits and persists IntradayOpportunity entries,
marks and exits, but cannot submit broker orders. A future live release requires a separate execution gateway and
risk gate with idempotent client order IDs, daily loss limits, exposure limits, halt/LULD handling,
short-borrow/SSR controls, kill switch and explicit operator authorization. Those concepts do not
belong in `alpaca_market_data`, `order_flow_engine`, `scalp_engine` or
`intraday_opportunity_engine`.
