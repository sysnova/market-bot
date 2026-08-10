# Alpaca market-data engine

Read-only ingress for Alpaca Stock Market Data. This engine never imports an
analysis engine and exposes no Trading API or order method.

## Inputs

- REST `/v2/stocks/bars` for bounded historical backfill, including pagination.
- REST `/v2/stocks/snapshots` for the latest trade, quote and bars.
- WebSocket `wss://stream.data.alpaca.markets/v2/<feed>` for authenticated live
  trades, quotes, minute bars, updated bars, daily bars and provider events.

Credentials and feed come from the existing `MARKETBOT_ALPACA_*` settings.
Secrets are unwrapped only inside `factory.py` and are never added to messages
or diagnostics.

## Outputs

Every provider record becomes an immutable `EventEnvelope` and is published via
the structural `EventPublisher` port. Production composition can pass the
repository's NATS JetStream adapter directly.

Subjects are stable and symbol-scoped:

```text
market.data.trade.<symbol>
market.data.quote.<symbol>
marketbot.v1.market.bar.<timeframe>.<symbol>
market.data.snapshot.<symbol>
```

Completed and updating bars use the same subject and carry the shared
`MarketBar` contract; `market.bar.received` and `market.bar.updated` distinguish
their finality. Financial values from trades, quotes and snapshots are decimal strings, never binary
floating-point values. Provider timestamps are UTC.

Event IDs are deterministic UUIDv7 values derived from provider timestamp and
message content. Replaying the same Alpaca record therefore retains the same
deduplication identity across reconnects and process restarts.

## Composition

```python
engine = build_alpaca_market_data_engine(
    settings,
    publisher=live_fanout,
    backfill_publisher=local_bus,
)
await engine.publish_snapshots(("AAPL", "MSFT"))
await engine.stream_once(("AAPL", "MSFT"))
```

REST backfill bars use `backfill_publisher`. The live composition binds that port directly to
the local analytical bus so historical warm-up hydrates the in-process `MarketBarStore` without
flooding NATS. Snapshots, WebSocket data, analysis results, and alerts continue through the live
fan-out publisher and may be mirrored to NATS.

`stream_once` represents one authenticated connection. The root supervisor owns
reconnection policy, health reporting and shutdown orchestration.

## Isolated signal backtesting

`marketbot market backtest` runs Core buy-signal engines and Signal Fusion entirely inside one
process. It reads historical OHLCV bars from Alpaca, but uses only an in-memory event bus and
in-memory Entry Watch/Opportunity stores. It never connects to operational NATS or PostgreSQL.

The required symbol list is also the simulated holdings list. Each symbol receives one share by
default; quantity can be overridden without changing the engines' signal logic.

```powershell
uv run marketbot market backtest 2026-08-05 `
  --symbols AAPL,MSFT `
  --simulated-date 2026-08-10 `
  --cadence-seconds 0.25 `
  --default-quantity 1 `
  --output .runtime/backtests/research-42.json
```

`--cadence-seconds` is the real wait between successive market-minute timestamps. Use `0` for an
immediate run. All symbols sharing a timestamp are delivered together before applying the wait.
The JSON artifact contains emitted alerts, stable Entry Signals, Signal Fusion transitions and
the paper opportunity lifecycle for the run.

## Tests

```powershell
uv run pytest app/alpaca_market_data/tests
```

All engine tests use fake HTTP, WebSocket and publication ports. They require no
network, credentials, database or NATS server.
