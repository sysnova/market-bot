# MarketBot

MarketBot is a Python 3.14 event-driven market analysis monorepo. Engines are isolated under
`app/<engine>/`; they communicate through stable contracts rather than direct imports.

The current MVP is deliberately analysis-only. Long v2, Swing v3, Intraday v4, Entry Watcher v5.1,
Entry Opportunity v2, Entry Recovery v1.1, Alert v3.2, Market History, and the Alpaca WebSocket ingress run as independent operating-system processes. NATS JetStream distributes
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

Native Windows and Linux/WSL environments are deliberately separate. A virtual environment
created by Linux contains links and interpreter paths that `uv.exe` cannot reuse on Windows.

On Windows PowerShell, use the repository bootstrap. It installs Python 3.14 when needed and
synchronizes the lockfile into `.venv-windows`:

```powershell
.\scripts\windows\setup-market-bot.ps1
```

Use `-Recreate` when you intentionally want to replace that disposable Windows environment.
Every script under `scripts/windows/` selects `.venv-windows` automatically.

On Linux or WSL, the launchers select `.venv-linux`. For a manual development shell, set the same
path before using `uv`:

```shell
export UV_PROJECT_ENVIRONMENT="$PWD/.venv-linux"
uv python install 3.14
uv sync --locked
```

For manual `uv` commands in Windows PowerShell, select the Windows environment for that shell:

```powershell
$env:UV_PROJECT_ENVIRONMENT = Join-Path (Get-Location) ".venv-windows"
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

The launcher starts all engines independently. Once ready, three visible consoles are arranged in
full-width rows: main control, analysis, and confirmed buys. Infrastructure and engine processes
remain hidden with separate logs under `.runtime/logs`. Use `-NoTileWindows` to keep manual window
positions, `-Once` for the legacy one-process diagnostic, or `-Symbols HIMS,ZETA` for a temporary
symbol override.

Validate one complete backfill/evaluation and exit:

```powershell
uv run marketbot live --once
```

Analyze one ticker through the coordinated one-shot mode:

```powershell
uv run market-bot -analyzer TICKER
```

The equivalent subcommand form is `uv run marketbot analyzer TICKER`. The ticker is
passed unchanged (after uppercase normalization and validation) to every applicable engine.
Long, Swing, Intraday, Entry Watcher, Alert, Market Rotation, LONG Portfolio,
PatreonCaps, Elliott Wave, Support Confirmation, Portfolio Flow, and Signal Fusion are
represented in one bounded report. Holdings-only and live-window engines are marked
`SKIPPED` when their gate does not apply. Peter Lynch and the SEC dilution scan are
intentionally excluded from this mode because they use slower external-provider paths.
Each remaining engine has an independent timeout, so one unavailable service cannot block
the complete report.

All operational entry points use the same versioned engine assembly. Inspect the effective
implementation, strategy artifact, and mode of every engine with:

```powershell
uv run marketbot assembly
```

The default definition is `configs/marketbot/7.3.0.yaml`; select another immutable assembly with
`MARKETBOT_DEFINITION_PATH`.

Individual distributed processes can also be operated directly:

```powershell
uv run marketbot alerts serve
uv run marketbot entry-watch serve
uv run marketbot entry-opportunity serve
uv run marketbot engine entry-recovery
uv run marketbot outbox serve
uv run marketbot engine long
uv run marketbot engine swing
uv run marketbot engine intraday
uv run marketbot market history
uv run marketbot market stream
```

By default, every process starts from the same universe as Stock Analyzer: the local PostgreSQL
watchlist plus symbols with positive holdings. The universe refreshes dynamically from PostgreSQL. Use
`--symbols AAPL,NVDA` for a temporary manual universe.

Historical bars use Alpaca's split adjustment. A single Market History process owns Alpaca REST,
stores the shared cache in local PostgreSQL, and refreshes registered requirements once per hour.
On restart, an engine requests only its missing interval over NATS Core and then reads PostgreSQL;
historical bars do not enter JetStream. Weekly bars are treated as complete only after the market
week closes. The WebSocket process never writes bars to PostgreSQL: it publishes live updates only
through NATS.

Alert v3.3 consumes stable `AnalysisResult`, Watcher transition, and setup-assessment contracts from
NATS and emits named `LONG_BUY_ZONE`,
`SWING_SETUP`, `EARLY_INTRADAY_WITHOUT_CONFIRMATION`, `ENTRY_CONFIRMED`, and
`HIGH_CONVICTION_BUY` notifications. A core entry now requires both the Intraday mature gate and
at least 1.5R from the Swing entry to its first resistance. Alerts appear with their actionable context: current price, buy zone,
invalidation, objective, Long/Swing/Intraday indicators, anchored and session VWAP, and the reasons
behind each engine decision. SEC warnings are emitted by the independent daily bot. The complete structured analyses are also appended
durably to one ledger per New York market date, for example
`.runtime/alerts/marketbot-alerts-2026-07-26.ndjson`. Only live market updates, engine results,
service health, and final alerts cross NATS. Historical REST bootstrap bars never do.

Portfolio Flow v2 observes the live quote/trade stream in rolling three-minute windows. Alongside
the existing red `PROTECT` alert for concentrated selling at the bid, it emits a cyan
`AGGRESSIVE ENTRY WATCH` with a short two-tone alarm when at least 70% of qualifying volume trades
at the ask and price rises at least 0.30%. This is deliberately an early flow alert, not an L1-L4
confirmation by itself.

The Entry Watcher remembers Long opportunities that are `EXTENDED`, `WATCH_PULLBACK`, `SETUP`, or
already in `BUY_ZONE`. It freezes the original zone and invalidation for 56 days by default,
persists every lifecycle transition in PostgreSQL, and waits for fresh Long, Swing, and Intraday
confirmation before publishing `ENTRY TRIGGERED`; Alert alone turns it into an L4 action alert. SEC dilution analysis is included
as an independent warning but never penalizes, gates, or invalidates an entry. Apply
`supabase/migrations/20260726180000_entry_watches.sql` and
`supabase/migrations/20260807010000_entry_opportunity_lifecycle.sql` after the foundation
migrations. If the
database or migration is unavailable, the distributed launcher stops before opening the market
stream so it cannot silently lose persistent opportunity tracking. The legacy `live` diagnostic
continues without it and logs that persistent entry watching is disabled.

Watcher and Opportunity write state plus their outgoing envelope to the PostgreSQL transactional
outbox. The headless outbox relay publishes committed rows to JetStream with at-least-once delivery;
a monitor or NATS interruption cannot create a committed-state/unpublished-event gap.

Entry Opportunity v2 is a separate assembled engine and process. It consumes source-agnostic
`EntrySignal` decisions and consolidates repeated watcher
IDs into one active ticker record. It
tracks L1-L4 checkpoints plus independent Intraday, Swing, and Long paper legs; records MFE, MAE,
session-close returns and realized gain/loss; and emits progress or closure events to the focused
buy monitor. Use `uv run marketbot entry-opportunity report` for open progress bars and audited success
rates.

Entry Watcher v5.1 preserves the recent-zone-touch and no-retest higher-low paths, persists its
confirmation checkpoint across restarts, and adds configurable zone-exit hysteresis. Confirmation
still requires efficient Intraday v4 evidence: strong evidence, a
five-minute higher low, and a second fresh reading at least three minutes later. First impulses
more than 0.50 ATR above their trigger or 2 ATR above EMA20 stay `WATCH`; they cannot produce L3/L4
until price and structure reset. A 30-minute post-trigger cooldown also suppresses immediate L4
duplicates for a newly calculated Long zone.

Entry Watcher v3 remains available for exact historical replay. In v3, a recent zone touch is
preserved through a moderate opening gap or breakaway.
For 72 hours it may confirm outside the frozen zone when extension stays within 4% and 0.75 ATR,
Intraday v3 confirms, anchored VWAP remains healthy, and live reward/risk is at least 2. This path
emits an early breakaway watch before `ENTRY TRIGGERED`; moves beyond the chase cap are labelled
`ENTRY EXTENDED WAIT` and require a retest.

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

## Local infrastructure

PostgreSQL and NATS run as independent Docker containers so PostgreSQL can be shared by local
projects:

```shell
docker start postgres-local marketbot-nats
docker ps --filter name=postgres-local --filter name=marketbot-nats
```

NATS 2.12 includes JetStream and PostgreSQL 17 listens on
`localhost:5432`, uses the `marketbot` database/user/password from `.env.example`, and persists its
database files in the repository-local `data/` directory (ignored by Git). NATS listens on
`localhost:4222`, exposes monitoring on `localhost:8222`, and persists in the Docker volume
`marketbot_nats-data`.

An optional trusted Windows peer can consume this JetStream through a split WireGuard tunnel
without changing either local endpoint. Existing engines keep `127.0.0.1:4222`; after WireGuard is
active Docker additionally publishes `10.77.77.1:4222` for the peer:

```powershell
$env:MARKETBOT_CONNECTOR_URL = "nats://10.77.77.1:4222"
uv run marketbot connector subscribe --engine swing
```

The operations guide covers key exchange, router forwarding, Docker replacement, replay by date,
and durable consumers.

See [architecture](docs/architecture.md), [engine interconnection map](docs/engine-interconnection-map.md),
[development](docs/development.md), and [operations](docs/operations.md) for repository-wide guidance.

On Ubuntu Desktop, install `tmux` and start the four-pane process monitor with:

```shell
sudo apt install tmux
./scripts/linux/start-market-bot.sh
```

The fourth pane, `LONG PORTFOLIO 2026`, loads persisted alerts from local PostgreSQL
and follows new allocation-aware LONG entries. The control pane also owns the
`long-portfolio-v1` engine, so `Ctrl+C` stops it together with the rest of MarketBot.

The same launcher also creates sibling `PatreonCaps`, `ElliottWave`, and
`SupportConfirmation` windows. Support Confirmation is a holdings-only analytical view and keeps its
reaction and structural-reversal scores separate. A new structural confirmation rings a
`REENTRY ARMED` prealert without replaying historical transitions.

`SignalFusion` is a fifth sibling window. Its upper pane shows support zone (`Z`), reaction (`R`),
structural confirmation (`S`), and every remaining cross-engine gate. Its lower pane shows only
current analytical `BUY_CONFIRMED` decisions; no broker execution is enabled.

The top pane controls lifecycle, the middle pane displays analyses, and the bottom pane displays
confirmed purchases. Pressing `Ctrl+C` in the control pane stops all MarketBot processes.
# Market Rotation local

MarketBot incluye un proceso independiente que reutiliza los perfiles sectoriales migrados en
PostgreSQL local, lee barras diarias del cache central, guarda cada ejecución en las tablas
`stock.market_rotation_*`, agrega candidatos con metadata `ROT` a la watchlist y publica el
reporte completo en `marketbot.v1.rotation.result` para que aparezca en el monitor JetStream.

Se inicia junto con `start-market-bot.ps1` cada 5 minutos, o manualmente una vez:

```powershell
uv run marketbot engine rotation --once
```
