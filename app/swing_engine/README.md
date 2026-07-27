# Swing engine

## Versiones

- `SwingEngineV1` / `SwingEngine`: reglas `1.1.1` originales.
- `SwingEngineV2`: versión activa `2.0.0`. Clasifica régimen con ADX y percentil de ATR,
  construye una zona alrededor del soporte técnico más próximo, informa distancia en ATR y
  calcula la relación riesgo/beneficio contra la resistencia observada.

V2 sólo marca estructura rota cuando el precio está al menos `1.5 ATR` bajo SMA50, la
pendiente de SMA20 es negativa, ADX confirma tendencia y `-DI > +DI`. Una corrección normal
de una acción high beta puede continuar como `setup` o `pullback`.

Deterministic swing analysis over completed daily bars and one intraday confirmation series
(`15Min` or `1Hour`). The engine looks for two constructive states: a controlled pullback in
an established uptrend, or an early breakout through 20-day resistance with confirming
volume. Broken daily structure is classified as avoid rather than converted into an order.

The calculation includes SMA 20/50, RSI(14), ATR(14), daily and intraday relative volume,
20-day resistance, 10-day support, and daily anchored VWAPs from the latest confirmed pivot
low and latest confirmed breakout. Each AVWAP includes its distance from current price,
contributes to the score, and can become the closest technical support used for invalidation.
The engine also reports risk percentage, risk in ATR units, and a two-R target used only to
assess asymmetry. Invalidation must remain below the observed price and within both an 8%
and 3-ATR risk budget for a favorable verdict.

`SwingEngine.analyze()` returns the shared `AnalysisResult` contract with `horizon=SWING`.
`evaluate()` returns the engine-owned detailed calculation. Both methods are pure; Alpaca,
NATS, persistence, alert presentation, and order execution belong to external adapters.

La composición live selecciona `SwingEngineV2`; V1 permanece disponible para reproducir
resultados anteriores.

Run the focused suite with:

```powershell
uv run pytest app/swing_engine/tests
```
