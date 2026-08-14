# Operations

The `marketbot` command is the stable operator entry point. Its `rules`, `strategy`, `audit`,
`supervisor`, and `infra` groups are registered even when an optional operator module is absent. An
absent module reports that fact and never pretends an operation succeeded.

On native Windows, initialize the locked Python environment once before the first launch:

```powershell
.\scripts\windows\setup-market-bot.ps1
```

Windows launchers always use `.venv-windows`; Linux and WSL launchers use `.venv-linux`. This avoids
the incompatible symlinks and interpreter paths produced when both operating systems share `.venv`.
For manual Windows `uv` commands in the examples below, first run:

```powershell
$env:UV_PROJECT_ENVIRONMENT = Join-Path (Get-Location) ".venv-windows"
```

```shell
uv run marketbot --help
uv run marketbot --version
```

## Versioned MarketBot assembly

Every process and the analyzer load the same exact-version definition. Inspect the effective
selection before starting services:

```powershell
uv run marketbot assembly
```

The default is `configs/marketbot/7.9.0.yaml`. Each engine entry separates:

- `implementation`: concrete Python behavior;
- `strategy`: embedded rules or an exact-version YAML artifact;
- `mode`: active, scheduled, or on-demand.

To deploy another complete, reviewed assembly, create a new immutable definition and select it:

```powershell
$env:MARKETBOT_DEFINITION_PATH = "configs/marketbot/7.9.0.yaml"
.\scripts\windows\start-market-bot.ps1
```

Strategy manifests live under `configs/rules/`. Entry confirmation `5.0.0` retains the v3
anchored-VWAP Swing gate and adds an Intraday anti-chase gate: at most 0.50 ATR above the trigger,
at most 2 ATR above EMA20, a strong five-minute higher low, and a second fresh Entry Watcher
confirmation after at least three minutes. Entry Watcher v5 may also reach `TRIGGERED` without a
prior Long-zone touch when the higher low persists and live reward/risk remains at least 2; Alert
is the only component that turns that transition into L4. Readiness summaries expose
`marketbot_definition_version`, `engine_implementation`, and `engine_strategy_version`; analytical
results retain their engine and entry-confirmation versions for outcome grouping.

The former confirmation setting remains as a deprecated atomic rollback switch. It changes the
compatible Swing/Intraday/Entry Watcher bundle in one place; no composition has its own version map:

```powershell
$env:MARKETBOT_ENTRY_CONFIRMATION_RULE_VERSION = "2.0.0"
.\scripts\windows\start-market-bot.ps1
```

Do not change an existing manifest in place. Add a new semantic version for every behavioral
change, retain old engine classes and manifests for reproducibility, and reference them from a new
MarketBot definition. Portfolio Flow V1/V2 rollback follows this same mechanism.

### Buy maturity presentation and measurement

MarketBot preserves each valid confirmation path and displays its maturity instead of collapsing all
buys into one label:

| Level | Evidence | Color | Native pattern |
|---|---|---|---|
| L1 Tactical recovery | Long + Intraday | Yellow | One tone |
| L2 Swing confirmed | Swing + Intraday | Blue | Two ascending tones |
| L3 High conviction | Long + Swing + Intraday | Green | Three ascending tones |
| L4 Core entry confirmed | Entry Watcher 5.1 `TRIGGERED`, including a separately tracked core recovery | Magenta | Three emphatic tones |

Long + Intraday is intentional: it preserves a tactical recovery when the Long backdrop remains
bullish but Swing has not recovered its daily/anchored-VWAP structure. Swing consumes daily and
15-minute/hourly bars, not weekly bars. A genuinely broken weekly thesis makes Long `AVOID` and
therefore cannot enter L1. A Long buy zone, Swing setup, armed watcher, or other incomplete analysis
remains silent and receives no maturity banner.

L4 describes accumulated evidence, not permission to chase price. Intraday v4 blocks L3 and the
Entry Watcher path to L4 while the quote is outside its efficient entry window. Entry Watcher 5.3
preserves V5.2 radar creation and tracking, but a first Intraday result that already passes every
mature, strong, higher-low, price-efficiency, Swing/AVWAP, freshness, extension, and live R/R gate
triggers L4 immediately at that result's price. A triggered symbol cannot be immediately rearmed
from a recalculated Long zone for 30 minutes, so one thesis does not produce duplicate alarms.

Entry Watcher 5.4 also recognizes an `EARLY_ENTRY` L1 before full maturity when the quote remains
within 4% and one Swing ATR of the frozen zone, Intraday confirms direction and efficiency, and live
reward/risk is at least 1.5. If the initial impulse escapes that window, the durable watch becomes
`IMPULSE_EXTENDED` instead of remaining ambiguously armed. It then tracks the impulse peak and low,
builds a 38.2%-61.8% retracement zone, and opens a pullback L1 only after a five-minute higher-low
reclaim with reward/risk of at least 2 back to the prior peak.

PatreonCaps, Long Portfolio, Signal Fusion, and Portfolio Flow publish independent analytical
families. They never acquire or impersonate an Alert L1-L4 level. Entry Opportunity tracks each
family/setup independently so a failed intraday leg, a continuing Swing leg, and a later recovery
can have separate paper outcomes.

The displayed price is the current or most recent tactical reference price, never the midpoint of a
stale buy zone. `app.alert_engine.evaluate_solid_buy_outcomes()` measures persisted buy alerts
against finalized one-minute bars without using bars at or before the signal. It reports the rule
manifest outcomes at 15, 30, and 60 minutes and the market close, plus 60-minute MFE, MAE, and the
first target/invalidation level reached. Engine and entry-confirmation rule versions remain attached
to each measurement so different rule generations can be compared independently.

## Realtime analysis

Configure Alpaca market-data credentials only in the ignored `.env` file. The independent SEC bot
additionally requires an identifiable User-Agent with a monitored contact email.

The default symbol universe is read directly from the local PostgreSQL `stock` schema configured by
`MARKETBOT_DATABASE_URL`. MarketBot combines active `watchlist_symbol` rows with positive active
`customer_holding` rows and refreshes every `MARKETBOT_UNIVERSE_REFRESH_SECONDS` (120 seconds by
default). Symbols added by analysis become available on the next refresh. There is no static
fallback list and no Supabase Edge Function is used at runtime. `--symbols` remains an explicit
one-run operator override.

After a dynamic change, `marketbot.v1.universe.changed.core` carries the desired replacement. Its
`consumer_warmup_required=true` assertion means each distributed Long/Swing/Intraday worker must
load its horizon-specific history before applying the snapshot. Health and ready files expose
`universe_policy` and `warmup_policy` for operator verification.

### Windows launcher

The PowerShell launcher resolves the repository location automatically. With no parameters it
starts every engine as an independent process. Three consoles remain visible and are tiled as
full-width horizontal rows on the primary monitor: supervisor/control at the top, analysis in the
middle, and confirmed buys at the bottom. Technical processes stay hidden and write individual
logs. The launcher starts Market History before the analytical engines; only after all consumers
are ready does it start the Alpaca WebSocket-to-NATS process:

```powershell
.\scripts\windows\start-market-bot.ps1
```

Common variants:

```powershell
.\scripts\windows\start-market-bot.ps1 -Once
.\scripts\windows\start-market-bot.ps1 -Symbols HIMS,ZETA
.\scripts\windows\start-market-bot.ps1 -NoNats -NoBell
.\scripts\windows\start-market-bot.ps1 -RuntimeRoot D:\MarketBotRuntime
.\scripts\windows\start-market-bot.ps1 -NoTileWindows
```

If the local execution policy blocks scripts, use:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows\start-market-bot.ps1
```

The equivalent low-level commands remain available:

```powershell
uv run marketbot live --once
uv run marketbot live
```

`-Once` preserves the legacy one-process diagnostic. The default distributed launcher supervises
all child processes and stops the children it created when the launcher exits. Logs live under
`.runtime/logs`. The deployment is analysis-only and cannot submit an order.

Both platform launchers consume the same canonical process graph. Inspect the effective commands,
readiness files, active slots, dependencies, and parallel startup batches without starting anything:

```powershell
uv run marketbot runtime-plan --runtime-root .runtime --no-bell
```

The JSON is derived from the selected MarketBot definition. Do not add a headless engine command or
startup dependency directly to a platform launcher; add it to the runtime plan and keep only
platform-specific window or process-control behavior in the script.

Options Gamma runs headless in `7.6.0` and refreshes every 600 seconds by default. It reads only
Alpaca market data, publishes NATS context, and never submits orders. An options-data failure is
fail-open and does not block the equity stream. Useful diagnostics:

```powershell
uv run marketbot engine options-gamma --once
uv run marketbot engine options-gamma --symbols AAPL,MSFT,NVDA --once
```

Tune it with `MARKETBOT_OPTIONS_GAMMA_REFRESH_SECONDS`,
`MARKETBOT_OPTIONS_GAMMA_DAYS_FORWARD`, `MARKETBOT_OPTIONS_GAMMA_STRIKE_RANGE_PERCENT`, and
`MARKETBOT_OPTIONS_GAMMA_CONCURRENCY`. Set `MARKETBOT_ALPACA_OPTIONS_FEED=opra` only when the Alpaca
subscription is entitled to that feed; otherwise the adapter uses Alpaca's default options feed.

Useful options:

```powershell
uv run marketbot live --no-nats
uv run marketbot live --runtime-root D:\MarketBotRuntime --bell
uv run marketbot live --symbols AAPL,NVDA
```

`--symbols` is a temporary override for that process and does not modify the shared watchlist.

Historical bar requests default to `MARKETBOT_ALPACA_ADJUSTMENT=split`. The centralized Market
History process is the sole owner of historical Alpaca REST bar downloads. Engines register their
symbol, timeframe, lookback, and maximum-bar requirements through the NATS Core request subject
`marketbot.rpc.v1.market.history.ensure`, then read the resulting bars from local PostgreSQL. This
RPC subject intentionally sits outside `marketbot.v1.>` and is not retained by JetStream.

Market History refreshes registered requirements once per hour. On restart, coverage in PostgreSQL
determines the missing interval, so only the gap plus a small overlap is downloaded. The WebSocket
ingress publishes live bars through NATS and never persists them. A restart within the hour can
therefore re-download at most that recent gap. PostgreSQL retention is bounded per symbol and
timeframe (currently 750 one-minute, 250 fifteen-minute, 650 hourly, 650 daily, and 500 weekly bars,
or a larger registered engine requirement plus margin).

Before the first run, apply `supabase/migrations/20260802170000_market_bar_cache.sql` to the local
`postgres-local` database. The directory name is retained for versioned schema compatibility; no
Supabase service or external database is queried at runtime.

MarketBot excludes the still-open weekly aggregate. REST backfills split large universes into batches of
`MARKETBOT_ALPACA_REST_BATCH_SIZE` symbols (20 by default), so Alpaca pagination is bounded per
batch instead of accumulating across the complete watchlist. The 15-minute warmup covers 14
calendar days, comfortably exceeding the 160-bar Swing working set without downloading 45 days
that the in-memory store discarded. Before publishing, each timeframe is trimmed to the maximum
working set consumed by its engines (221 weekly, 260 daily, 160 swing and 500 minute bars per
symbol), preventing unused history from entering the event bus.

In distributed mode, historical REST bars never enter an event bus: Market History persists them
once and each engine loads them into its private store from PostgreSQL. WebSocket updates, analysis
results, service health, and final alerts use NATS. Useful standalone process commands are:

```powershell
uv run marketbot alerts serve
uv run marketbot entry-watch serve
uv run marketbot entry-opportunity serve

# Open paper trades, maturity bars, L1-L4 win rate, and horizon gain/loss
uv run marketbot entry-opportunity report

# Live event-driven Entry Opportunity dashboard
uv run marketbot monitor entry-opportunity
uv run marketbot engine long
uv run marketbot engine swing
uv run marketbot engine intraday
uv run marketbot engine patreon-caps
uv run marketbot market history
uv run marketbot market stream
uv run marketbot monitor patreon-caps
uv run marketbot alerts patreon-caps
```

### Entry Opportunity history retention

`entry_opportunity_events` previously stored a complete Opportunity snapshot for every non-material
analysis. Maintenance is deliberately restricted to legacy rows whose **only** reason is
`long_term_evidence_updated`, `swing_evidence_updated`, or `intraday_evidence_updated`; lifecycle,
leg, maturity, close, and mixed-reason events are never candidates. At least the newest N events of
every opportunity are also protected.
Apply `20260809130000_entry_opportunity_event_retention.sql` before using `--apply`; it installs the
partial retention index and the narrowly scoped security-definer batch function. The runtime role
still has no direct `DELETE` grant.

Preview rows and approximate stored row bytes without changing PostgreSQL:

```powershell
uv run marketbot entry-opportunity prune-history --older-than-days 30 --retain-per-opportunity 100
```

Deletion requires the explicit `--apply` flag and commits one bounded batch at a time:

```powershell
uv run marketbot entry-opportunity prune-history --older-than-days 30 --retain-per-opportunity 100 --batch-size 1000 --apply
```

Take and verify a local PostgreSQL backup before applying retention. Deleting rows makes their space
reusable inside PostgreSQL but does not necessarily reduce the table file immediately. A normal
`VACUUM (ANALYZE)` may be scheduled afterward to update visibility/statistics. Returning disk space
to the operating system requires a separately reviewed maintenance operation such as `VACUUM FULL`
(exclusive table lock and full rewrite) or `pg_repack` (external extension/tool, extra temporary disk
space, and its own operational prerequisites). MarketBot does not execute any of these operations;
choose one only after checking free space, backups, locks, and the local maintenance window.

The alert ledger rotates by `America/New_York` market date and defaults to files such as
`.runtime\alerts\marketbot-alerts-2026-07-26.ndjson`. Each day is append-only, fsynced, and
deduplicated independently. Existing ledgers are retained; MarketBot does not delete old days.
A broker outage is logged
and local analysis continues; once connected, individual mirror failures likewise do not stop local
alerts.

## Independent daily SEC bot

SEC is deliberately absent from realtime startup. Run one bounded scan manually with:

```powershell
.\scripts\windows\run-sec-bot.ps1
.\scripts\windows\run-sec-bot.ps1 -LookbackDays 3 -Symbols HIMS,ZETA -NoNats
```

The default two-day inclusive filing-date window covers today and yesterday. The bot requests the
SEC submissions index for each symbol, keeps only dilution-related forms in that window, and skips
CompanyFacts when there is no matching recent filing. It neither downloads every historical form
nor executes orders. The window can be expanded up to 90 days with `-LookbackDays 90` in PowerShell
or `--lookback-days 90` in the cross-platform CLI. Alerts are printed and appended to the normal
daily NDJSON ledger.

For each matching company, document enrichment prioritizes and streams at most three primary SEC
documents, stops at 350,000 bytes, extracts auditable snippets/amounts/share quantities, and caches
the bounded text by accession under `.runtime/sec-documents`. Set
`MARKETBOT_SEC_DOCUMENT_MAX_FILINGS=0` to disable document reads.

For an on-demand evaluation even when no matching form exists in that window, use snapshot mode.
It downloads CompanyFacts for every selected symbol and publishes an analysis using those facts
plus any matching bounded filing metadata:

```bash
uv run marketbot sec snapshot --lookback-days 90 --symbols "ADUR,PM,NBIS" --no-nats
```

Snapshot mode still examines at most the 50 most recent submission records per company, does not
open historical submission indexes, and only reads the three prioritized primary documents. It
also considers recent 8-K, 6-K, 10-Q, 10-K, and 20-F documents for explicit textual signals.

Install the scan for the current Windows user at 20:00 local time each day (after the regular U.S.
market session):

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows\install-sec-daily-task.ps1
```

Use `-At 21:00`, `-LookbackDays 3`, or `-NoNats` to change the task. Re-running the installer for an
existing `MarketBotSECDaily` task requires `-Force`.

Production configuration should be provided as `MARKETBOT_*` environment variables by the runtime.
Do not mount or log `.env` files in production. Treat the URLs as secrets because they may contain
credentials.

JSON logs are the default. Bind a `correlation_id`, message ID, or command ID at entry points and
clear context at the end of every request/message to avoid context leakage between tasks.

The local workstation uses independent `postgres-local` and `marketbot-nats` Docker containers.
Production NATS and PostgreSQL lifecycle, backup, TLS, authentication, and monitoring belong to
the deployment platform.

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

## External JetStream connector over WireGuard

The external connector is a trusted pull consumer. It never changes the `MARKETBOT` stream and
does not require an engine to change its existing `nats://127.0.0.1:4222` or
`nats://localhost:4222` endpoint. NATS continues listening on `0.0.0.0:4222` **inside** Docker;
only Docker's host publications become restricted to loopback and the WireGuard interface.

Build the independently distributable client without packaging the MarketBot engines:

```powershell
uv build packages/marketbot-connector --wheel --out-dir dist/connector --clear
```

The resulting `marketbot_connector-*.whl` installs the `marketbot_connector` Python API and the
`marketbot-connector` CLI. Its only runtime dependencies are `nats-py`, `pydantic`, and `typer`.

Use this sequence on the Windows host:

To test only the Windows Firewall preparation before a WireGuard client exists, preview and then
create the standalone UDP rule from elevated PowerShell:

```powershell
.\scripts\windows\configure-wireguard-firewall.ps1
.\scripts\windows\configure-wireguard-firewall.ps1 -Apply
```

This only permits inbound UDP `51820`; it does not install WireGuard, start a listener, or touch
Docker/NATS. Therefore a port probe cannot prove the forwarding path until WireGuard is listening.
Remove this exact rule, if needed, with
`.\scripts\windows\configure-wireguard-firewall.ps1 -Remove -Apply`.

1. On the first client, generate its key and send only the returned `client_public_key` to the
   host administrator:

   ```powershell
   .\scripts\windows\configure-wireguard-client.ps1
   ```

   The script stores the private key under `%LOCALAPPDATA%\MarketBot\WireGuard`, never prints it,
   and reuses it on later runs. The VPN uses `10.77.77.0/24`: the host is
   `10.77.77.1`, the first client is `10.77.77.2`, and future clients receive consecutive unique
   addresses. Every machine must have its own key pair and `[Peer]` block.
2. From elevated PowerShell, preview and prepare the host configuration:

   ```powershell
   .\scripts\windows\configure-wireguard-host.ps1 `
     -ClientPublicKey "CLIENT_PUBLIC_KEY" -DryRun
   .\scripts\windows\configure-wireguard-host.ps1 `
     -ClientPublicKey "CLIENT_PUBLIC_KEY" -InstallIfMissing
   ```

   The command returns the host public key and writes a protected configuration file, but it does
   **not** register or start an automatic tunnel service. Open WireGuard, choose **Import tunnel(s)
   from file**, import the returned `config_path`, and use **Activate**/**Deactivate** manually.
3. Return the host public key, public IP or DDNS endpoint, and assigned client address. On the
   client, complete the configuration without exchanging its private key:

   ```powershell
   .\scripts\windows\configure-wireguard-client.ps1 `
     -HostPublicKey "HOST_PUBLIC_KEY" `
     -Endpoint "YOUR_PUBLIC_IP_OR_DDNS:51820" `
     -ClientAddress "10.77.77.2/24"
   ```

   Import the returned `config_path` in WireGuard and activate it manually. The split-tunnel route
   is limited to `10.77.77.1/32`; the client cannot use WireGuard to reach the LAN or other peers.
4. Reserve the host LAN address and forward only router UDP `51820` to
   `192.168.1.4:51820`. Never forward TCP `4222` or `8222`.
5. Activate `marketbot` manually. Once `Get-NetIPAddress -IPAddress 10.77.77.1` succeeds, preview
   and apply the Docker
   replacement:

   ```powershell
   .\scripts\windows\recreate-nats-for-wireguard.ps1
   .\scripts\windows\recreate-nats-for-wireguard.ps1 -Apply
   ```

   The script verifies `/data`, snapshots JetStream counters, preserves
   `marketbot_nats-data`, and restores a localhost-only container if WireGuard publication fails.
   It never removes the volume.

On the peer, connect through the VPN:

```powershell
$env:MARKETBOT_CONNECTOR_URL = "nats://10.77.77.1:4222"
uv run marketbot connector list-engines
uv run marketbot connector subscribe --engine swing --engine long-term
uv run marketbot connector subscribe --subject "marketbot.v1.alert.local.>"
uv run marketbot connector subscribe --engine swing `
  --start-at "2026-08-07T09:30:00-03:00"
```

Without `--start-at`, the connector receives the last retained message per concrete subject and
then follows live traffic. A date older than retention starts at the first available message and
prints a warning. The default is ephemeral; a named durable resumes its acknowledged position:

```powershell
uv run marketbot connector subscribe --engine swing --durable remote-windows
uv run marketbot connector reset remote-windows --yes
```

Use `--all` only when raw market bars and the DLQ are intentionally required. Output is JSON Lines;
invalid envelopes are represented as base64 evidence. Delivery is at least once, so downstream
side effects must deduplicate by `event_id`.

Acceptance checks from an external network are:

```powershell
# Host: latest handshake must be recent after the client activates its tunnel.
& "$env:ProgramFiles\WireGuard\wg.exe" show

# Client: succeeds only while WireGuard is active.
Test-NetConnection 10.77.77.1 -Port 4222

# These must not be reachable remotely.
Test-NetConnection YOUR_PUBLIC_IP_OR_DDNS -Port 4222
Test-NetConnection 10.77.77.1 -Port 8222
```

The UDP router path is validated by the WireGuard handshake, not by `Test-NetConnection`, which
tests TCP. If the public IP changes, update the client endpoint or use DDNS.

Watch all live MarketBot messages with formatted JSON from PowerShell:

```powershell
.\scripts\windows\watch-jetstream.ps1
# Or from the repository root:
.\watch-jetstream.cmd
```

Use `-Subject marketbot.v1.analysis.result.>` to narrow the subscription to analytical results.

Legacy releases created durable consumers with generated `mb_*` names. Preview and remove only
the disconnected consumers older than ten minutes with:

```powershell
uv run marketbot nats cleanup-consumers --dry-run
uv run marketbot nats cleanup-consumers --apply
```

The cleanup preserves connected consumers, stable named consumers, and every stream message.

## PatreonCaps v1 — análisis

PatreonCaps usa por defecto el artefacto inmutable
`configs/rules/patreon_caps/1.1.0.yaml`. Consume barras Alpaca y resultados completos de Long,
Swing V3 e Intraday V3 desde NATS; el universo, las tenencias y las asignaciones `PORT_YTD` se
obtienen de PostgreSQL local. No envia ordenes al broker.

La version `1.1.0` consolida SMA50/200 diaria y 1H, Golden/Death Cross, triangulo ascendente,
Wave 2 sobre Fibonacci 0.618 y retest del maximo de Wave 1. Para una comparacion o rollback se
puede ejecutar el artefacto anterior sin modificarlo:

```bash
uv run marketbot engine patreon-caps \
  --config-path configs/rules/patreon_caps/1.0.0.yaml
```

Antes del primer arranque, aplicar la migracion versionada al contenedor local `postgres-local`:

```powershell
$containerEnv = docker inspect postgres-local --format '{{range .Config.Env}}{{println .}}{{end}}'
$dbUser = (($containerEnv | Where-Object { $_ -like 'POSTGRES_USER=*' }) -split '=', 2)[1]
$dbName = (($containerEnv | Where-Object { $_ -like 'POSTGRES_DB=*' }) -split '=', 2)[1]
Get-Content .\supabase\migrations\20260801120000_patreon_caps.sql -Raw |
  docker exec -i postgres-local psql -v ON_ERROR_STOP=1 -U $dbUser -d $dbName
```

En Ubuntu/WSL, el launcher crea o reutiliza idempotentemente la ventana `PatreonCaps` dentro de la
sesion principal `marketbot`, con un panel de analisis y otro de alertas:

```bash
chmod +x ./scripts/linux/start-market-bot.sh
./scripts/linux/start-market-bot.sh
tmux attach-session -t marketbot
tmux select-window -t marketbot:PatreonCaps
```

Si la sesion ya existe, el launcher no la mata ni la duplica; solamente agrega la ventana cuando
falta. El panel de alertas recupera las ultimas 50 transiciones desde PostgreSQL y la campana suena
unicamente para `PATREON_CAPS_BUY`. En WSL, las confirmaciones `CONFIRMED_V`, `CONFIRMED_BASE` e
`IMPULSE_RETEST` reproducen un chime nativo de Windows de tres tonos ascendentes (650, 850 y
1100 Hz), incluso con TMUX detached. Si PowerShell no esta disponible, usa el bell del terminal.

## Entry Opportunities — seguimiento paper

El launcher de Ubuntu/WSL crea la ventana independiente `Opportunities`. La vista carga desde
PostgreSQL todas las oportunidades activas y el historial reciente; luego se redibuja inmediatamente
con cada evento de `marketbot.v1.entry-opportunity.transition.>`. Como las barras de un minuto pueden
actualizar precios sin producir un evento material, tambien relee PostgreSQL cada 30 segundos.

```bash
tmux select-window -t marketbot:Opportunities
uv run marketbot monitor entry-opportunity --history 100 --refresh-seconds 30
```

Cada oportunidad muestra estado, madurez actual y maxima, progreso, revision, precio original y
actual, zona, invalidacion, expiracion, motivo de cierre y ultimo evento observado. Debajo separa:

- checkpoints L1-L4 con entrada, salida, P/L abierto o G/L cerrado, MFE/MAE y retornos 15/30/60;
- legs Intraday/Swing/Long con entrada, target, invalidacion, estado y resultado independiente;
- ultimos analisis por horizonte y cantidad de analisis fuente.

Es un panel de seguimiento y auditoria paper. No envia ordenes al broker.

## Elliott Wave v0 — solo tenencias

El engine `elliott-wave@0.1.0` corre en paralelo y no altera Long, Swing, Intraday, AlertEngine ni
PatreonCaps. Su universo se obtiene exclusivamente de `stock.customer_holding` en PostgreSQL local,
filtrando posiciones activas con cantidad positiva; la watchlist no se incorpora. Publica un
`WaveAssessment` por tenencia en `marketbot.v1.elliott-wave.assessment.<SYMBOL>`, retenido por
JetStream durante 15 dias.

El launcher agrega una tercera ventana hermana llamada `ElliottWave`, con un panel que muestra la
hipotesis, score, confianza, zona, trigger, invalidacion y objetivos:

```bash
tmux select-window -t marketbot:ElliottWave
```

Es una lectura analítica: identifica candidatos de fin de onda 2/4 e impulso 3/5 activo, pero no emite
ordenes ni alertas de compra.

## Support Confirmation v0 — solo tenencias

`support-confirmation@0.1.0` busca soporte relevante de temporalidad mayor y clasifica tres
reacciones: recuperacion en V, construccion/ruptura de base y barrido de liquidez con reclaim. El
score `REACT` mide la validez del rebote local; `REV` solo sube cuando aparece evidencia de nueva
estructura alcista, especialmente higher high y higher low. Mientras falte esa estructura el panel
mantiene `B-RISK YES`, porque el rebote todavia puede ser una onda B.

El proceso usa exclusivamente tenencias positivas de PostgreSQL local y publica en subjects
propios de NATS:

```text
marketbot.v1.support-confirmation.assessment.<SYMBOL>
marketbot.v1.support-confirmation.transition.<STATE>.<SYMBOL>
```

El monitor emite una prealerta sonora `REENTRY ARMED` solo ante una transicion nueva a
`STRUCTURE_CONFIRMED` o `RETEST_CONFIRMED`. Las transiciones historicas no se reproducen para esta
alarma, de modo que reiniciar el panel no hace sonar estados viejos. `--no-bell` desactiva el sonido
sin interrumpir la publicacion NATS. Esta prealerta no equivale a una compra: `BUY_CONFIRMED` sigue
reservado para Signal Fusion cuando tambien pasan Long, timing, ejecucion, SEC, cartera y R/R.

JetStream conserva assessments y transiciones durante la retencion general de 15 dias y permite
restaurar el ultimo estado al reiniciar. No alimenta PatreonCaps, ElliottWave ni Alert en esta
fase de prueba, y no emite ordenes o alertas de compra.

El launcher crea una cuarta ventana hermana, independiente de las otras tres:

```bash
tmux select-window -t marketbot:SupportConfirmation
```

Para ejecutar una foto puntual sin dejar el proceso activo:

```bash
uv run marketbot engine support-confirmation --once
```

## Signal Fusion v0 — confirmacion multi-engine

`signal-fusion@0.2.0` consume exclusivamente los contratos persistidos en NATS para las tenencias
positivas. No vuelve a calcular indicadores ni llama Alpaca. El avance del soporte se muestra como
zona valida (`Z`), reaccion/defensa con score minimo 60 (`R`) y reversion estructural confirmada
(`S`). Los demas gates visibles son tendencia Long (`L`), timing Swing/Elliott (`T`), ejecucion
Intraday (`X`), dilucion SEC (`D`), cartera (`P`) y beneficio/riesgo (`RR`). PatreonCaps aparece como contexto,
pero no suma un voto independiente porque ya deriva parte de Long y Swing.

Los subjects propios son:

```text
marketbot.v1.signal-fusion.assessment.<SYMBOL>
marketbot.v1.signal-fusion.transition.<STATE>.<SYMBOL>
marketbot.v1.signal-fusion.buy-confirmed.<SYMBOL>
```

`BUY_CONFIRMED` exige que todos los gates pasen, una invalidacion por debajo de la entrada, un
objetivo por encima y `R/R >= 2`. La ausencia de un assessment SEC se muestra como `UNAVAILABLE` y
no suma puntos; un resultado SEC `CAUTION` o `AVOID` aplica veto. `BUY_CONFIRMED` es una señal
analítica: se persiste y evalúa como paper trade, pero no envía órdenes al broker.

`Z:Y R:Y S:N` no significa que falte soporte: indica que la zona sigue valida y fue defendida, pero
todavia no aparecieron el higher high, higher low y reversal score necesarios para confirmar una
nueva estructura alcista. Solo `S` participa como gate duro de compra.

El launcher agrega la ventana `SignalFusion` con dos paneles: evidencia/`ARMED` arriba y compras
confirmadas abajo. El panel inferior no hace sonar el replay historico, solo transiciones nuevas:

```bash
tmux select-window -t marketbot:SignalFusion
uv run marketbot engine signal-fusion --once
```
