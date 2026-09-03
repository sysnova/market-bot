# Swing engine

## Versiones

La definicion por defecto `7.47.0` selecciona `SwingEngineV15` con reglas `3.5.0`.
`STRUCTURE_INVALIDATED` significa que el cierre diario perdio el minimo de las
veinte ruedas previas despues de un breakout long fallido. `VOLATILITY_INVALIDATED`
significa que perdio el piso definido por el ATR del breakout. Ambos terminan el
ciclo de ese breakout long, pero siguen siendo evidencia para la tesis short.
V15 los admite junto con `ACTIVE` y `NEW_BREAKOUT_PENDING`, conservando los gates
de SMA20, ruptura minima de SMA50 y AVWAP del breakout. Un evento terminal no
habilita un short si el precio ya recupero el nivel original del breakout.
`RECOVERED`, `SUPERSEDED`, `EXPIRED` y `NONE` no habilitan esa tesis.

Esta correccion produce `short_structure_gate_passed` y `short_setup_id`; la
alerta `SHORT CONFIRMED` sigue exigiendo confirmacion intradia madura y niveles
ordenados `invalidation > entry > target`. Una caida diaria porcentual aislada no
reemplaza ninguna de esas condiciones. V14 y la definicion `7.46.0` permanecen
disponibles para replay y rollback. La prueba de integracion
`app/integration/tests/test_invalidated_long_short.py` verifica el recorrido de
las barras al analisis Swing, Intraday y la alerta, incluyendo el caso sin entrada.

La definicion historica `7.23.0` agrega
`SwingEngineV7`: devuelve `INSUFFICIENT_DATA` de forma segura con historia parcial y decide si la
ultima rueda diaria debe participar de la resistencia segun su fecha de mercado respecto de
`as_of`. Durante mercado abierto, la rueda completa anterior ya no queda excluida por posicion.

La definicion candidata `7.24.0` agrega `SwingEngineV8` y conserva sin cambios el carril
`TREND_CONTINUATION`. El segundo carril `STRUCTURE_RECOVERY` permite evaluar una recuperacion aun
cuando SMA20/50 y el stop estructural sigan reflejando dano. No compra el minimo: exige un selloff
reciente de al menos un ATR, rechazo diario con minimo y cierre posteriores mas altos, reclaim del
AVWAP del pivot, cuatro barras intradia de la misma rueda, piso ascendente, cierre alcista sobre
VWAP y ruptura de los tres maximos intradia previos. La invalidacion accionable se ubica bajo el
minimo de recuperacion con buffer de ATR; la invalidacion estructural original se conserva como
`structural_invalidation`. Ambos carriles exigen al menos `1.5R` hasta resistencia.

La definicion candidata `7.25.0` agrega `SwingEngineV9`. Conserva las confirmaciones diaria,
intradia y de riesgo de V8, pero desacopla la recuperacion del ultimo pivot general confirmado.
Su `recovery_avwap` nace en el minimo correctivo de las ultimas cinco ruedas que el propio carril
esta evaluando. Asi, una correccion fuerte no queda bloqueada por el AVWAP del impulso anterior;
el AVWAP general se sigue publicando como contexto y no se promueve a veto de recuperacion.

La definicion `7.26.0` agrega `SwingEngineV10`. El selloff deja de medirse solamente contra el
cierre de la rueda anterior: toma el maximo de las diez ruedas previas al minimo correctivo, por lo
que una caida distribuida en varias sesiones puede rearmar el carril aunque la estructura diaria
formal siga rota. La invalidacion accionable queda bajo ese minimo correctivo con buffer de ATR;
si el riesgo excede los limites, la compra espera. Cada minimo publica un `recovery_setup_id`
estable y un `recovery_avwap` nuevo, permitiendo deduplicar la confirmacion y habilitar un ciclo
distinto cuando aparece otra correccion.

La invalidacion tactica parte del minimo real de las
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
- `SwingEngineV7`: conserva V6, corrige la ventana de resistencia en evaluaciones live y hace
  explicita la inclusion de la ultima rueda mediante
  `resistance_latest_completed_bar_included`.
- `SwingEngineV8`: conserva V7 y publica `entry_lane`,
  `continuation_entry_gate_passed` y `recovery_entry_gate_passed`; una recuperacion confirmada usa
  clasificacion `recovery` y separa riesgo tactico de riesgo estructural.
- `SwingEngineV9`: conserva V8 y exige el reclaim de un `recovery_avwap` anclado al minimo
  correctivo reciente; publica su ancla, valor, distancia porcentual y estado del gate.
- `SwingEngineV10`: conserva V9, reconoce selloffs multisesion, usa el minimo correctivo como
  invalidacion y publica identidad/rearmado por ciclo de recuperacion.

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

El backtest integrado publica `three_swing_model_comparison`, que alinea causalmente Swing diario,
4HGERI y SwingTrade. Tambien publica `swing_model_confirmation_summary`: separa
`FAVORABLE`, `swing_entry_gate_passed`, conteos por `entry_lane`, confirmaciones completas, razones
de rechazo y banderas de riesgo dentro de la ventana simulada; los resultados de bootstrap quedan
fuera de esos conteos.

Ejecutar la suite focalizada con:

```powershell
uv run pytest app/swing_engine/tests
```
