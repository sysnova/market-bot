# MarketBot

MarketBot is a Python 3.14 event-driven market analysis monorepo. Engines are isolated under
`app/<engine>/`; they communicate through stable contracts rather than direct imports.

The current MVP is deliberately analysis-only. The continuous process consumes Alpaca Stock Market
Data, publishes versioned events, runs the market analytical horizons, and shows local alerts. An
independent daily bot checks recent SEC EDGAR filings. There is no order adapter and configuration
enforces `MARKETBOT_ALPACA_EXECUTION_ENABLED=false`.

## Prerequisites

- [`uv`](https://docs.astral.sh/uv/) 0.11 or newer
- Python 3.14 (installed automatically by `uv` when downloads are available)
- Docker only if you choose to run the optional local NATS/PostgreSQL stack

No local Docker daemon, NATS instance, database, or secret is required for quality checks and unit
tests.

## Bootstrap

```shell
uv python install 3.14
uv sync --locked
uv run marketbot --help
```

Copy `.env.example` to `.env` only when running components that need infrastructure. Pydantic reads
all process settings from the `MARKETBOT_` namespace.

## Run the analytical MVP

On Windows, start the continuous bot from PowerShell without writing the underlying `uv` command:

```powershell
.\scripts\windows\start-market-bot.ps1
```

The launcher uses the configured Supabase universe, local NATS service, and terminal alerts by
default. Use `-Once` for one evaluation or `-Symbols HIMS,ZETA` for a temporary symbol override.

Validate one complete backfill/evaluation and exit:

```powershell
uv run marketbot live --once
```

Open the realtime Alpaca WebSocket and keep analyzing until Ctrl+C:

```powershell
uv run marketbot live
```

By default, MarketBot uses the same universe as Stock Analyzer: the Supabase watchlist plus symbols
with positive holdings. It refreshes that universe every 120 seconds and reconnects Alpaca only
when it changes. `MARKETBOT_ALPACA_WATCHLIST` is merged by the shared service and remains the local
fallback if Supabase is unavailable. Use `--symbols AAPL,NVDA` for a temporary manual universe.

Historical bars use Alpaca's split adjustment. Weekly bars are treated as complete only after the
market week closes, and the recent weekly context is refreshed each Saturday at 02:00 New York
time so long-term moving averages stay current without restarting the bot.

Alerts appear in the terminal with their actionable context: current price, buy zone,
invalidation, objective, Long/Swing/Intraday indicators, anchored and session VWAP, and the reasons
behind each engine decision. SEC warnings are emitted by the independent daily bot. The complete structured analyses are also appended
durably to one ledger per New York market date, for example
`.runtime/alerts/marketbot-alerts-2026-07-26.ndjson`. Market, analysis, and alert events are mirrored to the
local NATS JetStream stream `MARKETBOT`; `--no-nats` keeps a fully local in-process pipeline when
the broker is intentionally unavailable.

The Entry Watcher remembers Long opportunities that are `EXTENDED`, `WATCH_PULLBACK`, `SETUP`, or
already in `BUY_ZONE`. It freezes the original zone and invalidation for 56 days by default,
persists every lifecycle transition in PostgreSQL, and waits for fresh Long, Swing, and Intraday
confirmation before emitting an `ENTRY TRIGGERED` action alert. SEC dilution analysis is included
as an independent warning but never penalizes, gates, or invalidates an entry. Apply
`supabase/migrations/20260726180000_entry_watches.sql` after the foundation migrations. If the
database or migration is unavailable, realtime analysis continues and logs that persistent entry
watching is disabled.

Run the bounded SEC scan manually with `.\scripts\windows\run-sec-bot.ps1`. It checks only the
configured recent filing-date window (two days by default), considers dilution-related forms, and
does not backfill historical filings. See the operations guide to install it as a daily Windows
Scheduled Task.

## Development gates

```shell
uv run ruff check .
uv run pyright
uv run pytest -m "not integration"
```

The test suite enforces at least 80% branch-aware coverage. Tests needing external services must use
the `integration` marker.

## Optional local infrastructure

For hosts that already have Docker Compose:

```shell
docker compose up -d
docker compose ps
```

This starts NATS 2.12 with JetStream and PostgreSQL 17. It is a convenience for integration work,
not a prerequisite for local development.

See [architecture](docs/architecture.md), [development](docs/development.md), and
[operations](docs/operations.md) for repository-wide guidance.
