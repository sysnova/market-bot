# Operations

The `marketbot` command is the stable operator entry point. Its `rules`, `strategy`, `audit`,
`supervisor`, and `infra` groups are registered even when an optional operator module is absent. An
absent module reports that fact and never pretends an operation succeeded.

```shell
uv run marketbot --help
uv run marketbot --version
```

## Realtime analysis

Configure Alpaca market-data credentials only in the ignored `.env` file. SEC analysis additionally
requires an identifiable User-Agent with a monitored contact email.

The default symbol universe is shared with Stock Analyzer through its read-only Supabase Edge
Function contract. Configure `MARKETBOT_SUPABASE_URL` and
`MARKETBOT_SUPABASE_DESKTOP_API_KEY`; MarketBot fetches `watchlist` and `holdings`, keeps only
positive positions, merges `MARKETBOT_ALPACA_WATCHLIST` as fallback, and refreshes every
`MARKETBOT_UNIVERSE_REFRESH_SECONDS` (120 seconds by default).

```powershell
uv run marketbot live --once
uv run marketbot live
```

`--once` performs the four historical backfills, snapshots, SEC refresh, and one evaluation before
closing. Without it, MarketBot opens the Alpaca WebSocket for trades, quotes, minute bars, updated
bars, and daily bars, reconnecting with bounded exponential backoff. Ctrl+C stops the foreground
process. The process is analysis-only and cannot submit an order.

Useful options:

```powershell
uv run marketbot live --no-nats
uv run marketbot live --runtime-root D:\MarketBotRuntime --bell
uv run marketbot live --symbols AAPL,NVDA
```

`--symbols` is a temporary override for that process and does not modify the shared watchlist.

Historical bar requests default to `MARKETBOT_ALPACA_ADJUSTMENT=split`. MarketBot excludes the
still-open weekly aggregate and refreshes recent completed weekly bars every Saturday at 02:00
America/New_York. The refresh temporarily mutes historical reactions and then reevaluates only the
long-term horizon.

The alert ledger defaults to `.runtime\alerts\marketbot-alerts.ndjson`. A broker outage is logged
and local analysis continues; once connected, individual mirror failures likewise do not stop local
alerts.

Production configuration should be provided as `MARKETBOT_*` environment variables by the runtime.
Do not mount or log `.env` files in production. Treat the URLs as secrets because they may contain
credentials.

JSON logs are the default. Bind a `correlation_id`, message ID, or command ID at entry points and
clear context at the end of every request/message to avoid context leakage between tasks.

`compose.yaml` is solely a workstation convenience. Production NATS and PostgreSQL lifecycle,
backup, TLS, authentication, and monitoring belong to the deployment platform.

## Native NATS service on Windows

For a persistent Windows development broker, run the repository installer from an elevated
PowerShell process:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/windows/install-nats-service.ps1
```

The installer downloads the pinned official NATS archive, verifies its SHA-256 checksum, and
registers `MarketBotNATS` as an automatic Windows service running as `NetworkService`. The client
and monitoring listeners bind only to localhost:

- `nats://127.0.0.1:4222`
- `http://127.0.0.1:8222`

JetStream state and logs live under `C:\ProgramData\MarketBot\NATS`. The service can be inspected
and controlled with standard Windows commands:

```powershell
Get-Service MarketBotNATS
Start-Service MarketBotNATS
Stop-Service MarketBotNATS
Test-NetConnection 127.0.0.1 -Port 4222
(Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8222/varz).StatusCode
```

Validate MarketBot against the native broker with:

```powershell
$env:NATS_URL = "nats://127.0.0.1:4222"
uv run pytest app/event_bus/tests/test_nats_integration.py -m integration --no-cov
```
