# Long-term engine

## Versiones

- `LongTermEngineV1` / `LongTermEngine`: reglas históricas `1.1.1`, conservadas para
  reproducibilidad.
- `LongTermEngineV2`: versión activa `2.0.0`. Agrega ADX, `+DI/-DI`, percentil de ATR,
  régimen local y distancia a la zona de compra expresada en ATR. El régimen ajusta la
  calidad de la entrada, pero no convierte por sí solo una señal en una orden.

Pure long-horizon technical analysis over normalized daily and completed weekly OHLCV bars.
The engine separates `setup_score` (quality of the underlying structure) from `entry_score`
(quality of the current location), so a strong company chart can remain interesting without
turning an extended price into an actionable alert.

The input is a frozen `LongTermContext` built from the shared `MarketBar` contract. Bars must be strictly chronological, unique, UTC,
positive, internally consistent, and no later than `as_of`. Market-data ingestion is owned by
an adapter outside this engine; incomplete weekly bars must be removed before calling it.

The analysis covers:

- daily SMA 20/50/150/200 and weekly SMA 10/30/50/200;
- weekly price distance from SMA 200 as a percentage, with an explicit risk flag when below it;
- daily and weekly RSI(14), relative volume, and recent distribution weeks;
- trend-template criteria, higher-low structure, 52-week range, support and resistance;
- a stable weekly buy zone and invalidation level derived only from completed bars;
- deterministic setup, entry, and combined scores with machine-readable reasons.

`LongTermEngine.analyze()` performs no I/O and returns the shared `AnalysisResult` contract
with `horizon=LONG_TERM`; `evaluate()` exposes the engine-owned detailed result. It has no
dependency on Alpaca, NATS, SEC, PostgreSQL, or another engine. Root integration is
responsible for translating the result into events and local monitor alerts. The classifications describe chart
state (`buy_zone`, `setup`, `watch_pullback`, `extended`, `avoid`, `insufficient_data`); they
are analysis signals, not instructions to submit an order.

The weekly SMA 200 is currently contextual: it is emitted as a metric and risk/support signal but
does not by itself grant or veto `buy_zone`. That classification continues to require the complete
setup and entry conditions described above.

Root integration supplies split-adjusted history, excludes the open market week, retains 220
completed weekly bars, and refreshes the recent weekly slice after each Friday close.

La composición live utiliza explícitamente `LongTermEngineV2`; V1 continúa importable para
comparaciones controladas y para reproducir alertas históricas.

Run its isolated tests with:

```powershell
uv run pytest app/long_term_engine/tests
```
