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
engine = build_alpaca_market_data_engine(settings, publisher=nats_bus)
await engine.publish_snapshots(("AAPL", "MSFT"))
await engine.stream_once(("AAPL", "MSFT"))
```

`stream_once` represents one authenticated connection. The root supervisor owns
reconnection policy, health reporting and shutdown orchestration.

## Tests

```powershell
uv run pytest app/alpaca_market_data/tests
```

All engine tests use fake HTTP, WebSocket and publication ports. They require no
network, credentials, database or NATS server.
