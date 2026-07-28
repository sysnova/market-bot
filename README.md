# MarketBot

MarketBot is a Python 3.14 event-driven market analysis monorepo. Engines are isolated under
`app/<engine>/`; they communicate through stable contracts rather than direct imports.

The current MVP is deliberately analysis-only. Long v2, Swing v2, Intraday v2, Entry Watcher v2,
Alert v2, and the Alpaca WebSocket ingress run as independent operating-system processes. NATS JetStream distributes
live market bars and analytical results between them. An independent daily bot checks recent SEC
EDGAR filings. There is no order adapter and configuration enforces
`MARKETBOT_ALPACA_EXECUTION_ENABLED=false`.

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

The launcher starts Alert, Entry Watcher, Long, Swing, and Intraday independently. Each analytical
engine loads only its own REST history into its own in-memory store. After all five consumers are ready, the launcher
starts the independent Alpaca WebSocket-to-NATS process. Use `-Once` for the legacy one-process
diagnostic evaluation or `-Symbols HIMS,ZETA` for a temporary symbol override.
Alert opens in one visible local monitor; infrastructure and engine processes remain hidden with
separate logs under `.runtime/logs`.

Validate one complete backfill/evaluation and exit:

```powershell
uv run marketbot live --once
```

Individual distributed processes can also be operated directly:

```powershell
uv run marketbot alerts serve
uv run marketbot entry-watch serve
uv run marketbot engine long
uv run marketbot engine swing
uv run marketbot engine intraday
uv run marketbot market stream
```

By default, every process starts from the same universe as Stock Analyzer: the Supabase watchlist
plus symbols with positive holdings. `MARKETBOT_ALPACA_WATCHLIST` remains the local fallback. Use
`--symbols AAPL,NVDA` for a temporary manual universe.

Historical bars use Alpaca's split adjustment. Weekly bars are treated as complete only after the
market week closes. The distributed Long process receives completed daily and weekly updates from
NATS after its private bootstrap.

Alert v2 consumes every engine's `AnalysisResult` from NATS and emits named `LONG_BUY_ZONE`,
`SWING_SETUP`, `ENTRY_CONFIRMED`, and `HIGH_CONVICTION_BUY` notifications. Alerts appear with their actionable context: current price, buy zone,
invalidation, objective, Long/Swing/Intraday indicators, anchored and session VWAP, and the reasons
behind each engine decision. SEC warnings are emitted by the independent daily bot. The complete structured analyses are also appended
durably to one ledger per New York market date, for example
`.runtime/alerts/marketbot-alerts-2026-07-26.ndjson`. Only live market updates, engine results,
service health, and final alerts cross NATS. Historical REST bootstrap bars never do.

The Entry Watcher remembers Long opportunities that are `EXTENDED`, `WATCH_PULLBACK`, `SETUP`, or
already in `BUY_ZONE`. It freezes the original zone and invalidation for 56 days by default,
persists every lifecycle transition in PostgreSQL, and waits for fresh Long, Swing, and Intraday
confirmation before emitting an `ENTRY TRIGGERED` action alert. SEC dilution analysis is included
as an independent warning but never penalizes, gates, or invalidates an entry. Apply
`supabase/migrations/20260726180000_entry_watches.sql` after the foundation migrations. If the
database or migration is unavailable, the distributed launcher stops before opening the market
stream so it cannot silently lose persistent opportunity tracking. The legacy `live` diagnostic
continues without it and logs that persistent entry watching is disabled.

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
