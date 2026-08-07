# Intraday engine

## Versiones

- `IntradayEngineV1` / `IntradayEngine`: reglas originales `1.0.0`.
- `IntradayEngineV2`: versión activa `2.0.0`. Conserva breakout y VWAP reclaim de V1 y
  agrega régimen intradiario, ubicación del cierre dentro de la vela, aceleración del volumen,
  higher low de 5 minutos y calidad de confirmación `weak/standard/strong`.
- `IntradayEngineV3`: regla reproducible `3.0.0`; agrega persistencia y stops adaptados a ATR.
- `IntradayEngineV4`: regla activa `4.0.0`; evita perseguir el primer impulso. Un breakout o
  reclaim sólo queda `FAVORABLE` cuando conserva precio eficiente (máximo 0,50 ATR sobre el
  trigger y 2 ATR sobre EMA20), confirmación `strong` y higher low de cinco minutos. En otro caso
  emite `WATCH` con `late_entry_wait_retest` o `mature_retest_pending`.

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

La composición live utiliza `IntradayEngineV4` por defecto; V1-V3 continúan disponibles para
rollback y replay.

Run focused verification with:

```powershell
uv run pytest app/intraday_engine/tests
uv run ruff check app/intraday_engine
uv run pyright app/intraday_engine
```
