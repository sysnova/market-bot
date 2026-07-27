# Intraday engine

## Versiones

- `IntradayEngineV1` / `IntradayEngine`: reglas originales `1.0.0`.
- `IntradayEngineV2`: versión activa `2.0.0`. Conserva breakout y VWAP reclaim de V1 y
  agrega régimen intradiario, ubicación del cierre dentro de la vela, aceleración del volumen,
  higher low de 5 minutos y calidad de confirmación `weak/standard/strong`.

Un trigger V1 con evidencia débil se degrada a `WATCH` en V2. Entry Watcher sólo acepta una
confirmación intradiaria favorable y con un setup alcista explícitamente reconocido.

Pure, deterministic analysis of completed Alpaca-normalized `MarketBar` history.
The engine consumes at least 30 one-minute bars and optionally uses five-minute
bars as confirmation. It performs no network, database, clock or filesystem I/O.

The analysis covers:

- session VWAP and bullish reclaim / bearish rejection;
- EMA 9/20 and five-bar momentum;
- latest relative volume against the prior 20 bars;
- ATR 14 and the prior 20-bar range;
- bullish breakout and bearish breakdown;
- structural invalidation, objective, percentage risk and reward/risk levels.

Its only cross-engine output is `AnalysisResult` with horizon `INTRADAY`.
Levels describe an analytical setup for a human alert. There are deliberately no
orders, quantities, position state, execution calls or Trading API concepts.

`AnalysisResult.context_hash` and `analysis_id` are deterministic for identical
input, making repeated evaluations reproducible and auditable. Reasons and all
calculated evidence are exposed through stable metrics.

La composición live utiliza `IntradayEngineV2`; V1 continúa disponible.

Run focused verification with:

```powershell
uv run pytest app/intraday_engine/tests
uv run ruff check app/intraday_engine
uv run pyright app/intraday_engine
```
