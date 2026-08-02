# Operations

The `marketbot` command is the stable operator entry point. Its `rules`, `strategy`, `audit`,
`supervisor`, and `infra` groups are registered even when an optional operator module is absent. An
absent module reports that fact and never pretends an operation succeeded.

```shell
uv run marketbot --help
uv run marketbot --version
```

## Versioned entry-confirmation rules

Entry confirmation is selected by an exact, rollback-safe artifact coordinate:

```powershell
$env:MARKETBOT_ENTRY_CONFIRMATION_RULE_VERSION = "3.0.0"
.\scripts\windows\start-market-bot.ps1
```

The manifests live under `configs/rules/entry_confirmation/`. Version `3.0.0` adds an
anchored-VWAP Swing gate, two-close or five-minute persistence, magnitude-aware momentum, and a
minimum tactical stop based on price and ATR. Every Swing and Intraday result records both its
`engine_version` and `entry_confirmation_rule_version`, so alert outcomes can be grouped by the
exact rules that produced them.

Rollback does not require a code revert. Stop MarketBot and restart it with the previous exact
coordinate:

```powershell
$env:MARKETBOT_ENTRY_CONFIRMATION_RULE_VERSION = "2.0.0"
.\scripts\windows\start-market-bot.ps1
```

Do not change an existing manifest in place. Add a new semantic version for every behavioral
change, and retain old engine classes and manifests for reproducibility.

## Realtime analysis

Configure Alpaca market-data credentials only in the ignored `.env` file. The independent SEC bot
additionally requires an identifiable User-Agent with a monitored contact email.

The default symbol universe is read directly from the local PostgreSQL `stock` schema configured by
`MARKETBOT_DATABASE_URL`. MarketBot combines active `watchlist_symbol` rows with positive active
`customer_holding` rows and refreshes every `MARKETBOT_UNIVERSE_REFRESH_SECONDS` (120 seconds by
default). Symbols added by analysis become available on the next refresh. There is no static
fallback list and no Supabase Edge Function is used at runtime. `--symbols` remains an explicit
one-run operator override.

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
uv run marketbot engine long
uv run marketbot engine swing
uv run marketbot engine intraday
uv run marketbot engine patreon-caps
uv run marketbot market history
uv run marketbot market stream
uv run marketbot monitor patreon-caps
uv run marketbot alerts patreon-caps
```

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
nor executes orders. Alerts are printed and appended to the normal daily NDJSON ledger.

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

## PatreonCaps v1 SHADOW

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

## Elliott Wave v0 SHADOW — solo tenencias

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

Es una lectura SHADOW: identifica candidatos de fin de onda 2/4 e impulso 3/5 activo, pero no emite
ordenes ni alertas de compra.

## Support Confirmation v0 SHADOW — solo tenencias

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
restaurar el ultimo estado al reiniciar. No alimenta PatreonCaps, ElliottWave ni Alert v2 en esta
fase de prueba, y no emite ordenes o alertas de compra.

El launcher crea una cuarta ventana hermana, independiente de las otras tres:

```bash
tmux select-window -t marketbot:SupportConfirmation
```

Para ejecutar una foto puntual sin dejar el proceso activo:

```bash
uv run marketbot engine support-confirmation --once
```

## Signal Fusion v0 SHADOW — confirmacion multi-engine

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
no suma puntos; un resultado SEC `CAUTION` o `AVOID` aplica veto. Todo permanece en SHADOW y no
envia ordenes al broker.

`Z:Y R:Y S:N` no significa que falte soporte: indica que la zona sigue valida y fue defendida, pero
todavia no aparecieron el higher high, higher low y reversal score necesarios para confirmar una
nueva estructura alcista. Solo `S` participa como gate duro de compra.

El launcher agrega la ventana `SignalFusion` con dos paneles: evidencia/`ARMED` arriba y compras
confirmadas abajo. El panel inferior no hace sonar el replay historico, solo transiciones nuevas:

```bash
tmux select-window -t marketbot:SignalFusion
uv run marketbot engine signal-fusion --once
```
