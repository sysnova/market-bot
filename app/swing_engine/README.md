# Swing engine

## Versiones

La composicion activa usa `SwingEngineV6`. La invalidacion tactica parte del minimo real de las
ultimas diez ruedas y nunca usa un AVWAP como soporte automatico. La resistencia operativa usa
cierres diarios; el maximo de mecha se publica por separado como `liquidity_high`. Un breakout que
vuelve bajo el nivel roto dentro de cinco ruedas entra en una maquina de estados auditable. El veto
termina por recuperacion, destruccion estructural, distancia de cinco ATR historicos, expiracion de
sesenta ruedas o confirmacion de una base nueva. V1-V5 permanecen disponibles para replay y rollback.

- `SwingEngineV1` / `SwingEngine`: reglas `1.1.1` originales.
- `SwingEngineV2`: clasifica regimen con ADX y percentil de ATR, construye una zona alrededor del
  soporte tecnico mas proximo e informa distancia en ATR.
- `SwingEngineV3`: agrega la compuerta de AVWAP.
- `SwingEngineV4`: exige al menos `1.5R` hasta resistencia.
- `SwingEngineV5`: separa soporte estructural, resistencia por cierres y mechas de liquidez; tambien
  bloquea falsos breakouts pendientes de recuperacion.
- `SwingEngineV6`: conserva los gates de V5 y agrega el ciclo `ACTIVE`,
  `NEW_BREAKOUT_PENDING`, `RECOVERED`, `STRUCTURE_INVALIDATED`,
  `VOLATILITY_INVALIDATED`, `EXPIRED` y `SUPERSEDED`.

V2 solo marca estructura rota cuando el precio esta al menos `1.5 ATR` bajo SMA50, la pendiente de
SMA20 es negativa, ADX confirma tendencia y `-DI > +DI`. Una correccion normal puede continuar como
`setup` o `pullback`, pero V5 no la vuelve accionable si el riesgo estructural, el R/R por cierres o
la recuperacion de un falso breakout no pasan sus compuertas.

El analisis usa barras diarias completas y una serie de confirmacion `15Min` o `1Hour`. Incluye
SMA20/50, RSI(14), ATR(14), volumen relativo diario e intradia, soporte estructural de diez ruedas,
resistencia de cierres de veinte ruedas y AVWAP diario desde el ultimo pivot y breakout confirmados.
Los AVWAP aportan ubicacion y confluencia; no definen la invalidacion. `liquidity_high` conserva la
mecha maxima como advertencia sin convertirla en resistencia ni target operativo.

`SwingEngine.analyze()` retorna el contrato compartido `AnalysisResult` con `horizon=SWING`.
`evaluate()` retorna el detalle propio del engine. Ambos son puros; Alpaca, NATS, persistencia,
presentacion de alertas y ejecucion pertenecen a adaptadores externos.

Ejecutar la suite focalizada con:

```powershell
uv run pytest app/swing_engine/tests
```
