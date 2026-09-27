# ASTS / ASTN: revisión de Leveraged Thesis, viernes 25 de septiembre de 2026

El replay reproduce **cero confirmaciones SHORT de ASTS y cero compras de ASTN**. Intraday sí detectó dos señales bajistas maduras, pero la estrategia operativa exige además una tesis SHORT diaria de Swing. Ese requisito no se cumplió en ninguna de las 26 lecturas regulares de Swing. El filtro de soporte habría bloqueado también ambas señales.

La ausencia de compra concuerda con las reglas vigentes. No prueba que el movimiento intradía fuera inexistente: ASTS retrocedió 3,47% entre el máximo y el mínimo de la rueda. La ruta actual no está diseñada para comprar el inverso ante cualquier caída intradía.

## Alcance y datos

- Rueda regular: 09:30–16:00 de Nueva York, 10:30–17:00 de Argentina.
- Definición local efectiva: MarketBot **7.77.0**, Leveraged Thesis **1.3.0**, Swing **16.0.0 / estrategia 3.5.0**, Intraday **10.0.0 / estrategia 1.6.0**. Los eventos históricos de Swing e Intraday usan esas versiones.
- Fuente operativa: lectura directa del stream local `MARKETBOT`, incluyendo `marketbot.v1.analysis.result.>` y los subjects de ASTS/ASTN pertinentes. Se guardaron 14.364 eventos del viernes por fecha de persistencia. Se separaron las publicaciones de bootstrap con datos del jueves de las observaciones regulares del viernes.
- Cobertura regular real: **390 análisis Intraday, 26 Swing y 496 estados de Order Flow de ASTN**. No hubo alerta `SHORT CONFIRMED` ni señal `LEVERAGED_THESIS` para estos símbolos en la extracción del viernes. Las alertas de ASTS fueron `AGGRESSIVE ENTRY WATCH` y `PROTECT`, de otras rutas.
- Recomputación independiente: **390 velas SIP de un minuto de ASTS**, con 1.912 barras de calentamiento de los timeframes requeridos; fuentes históricas de Alpaca. ASTN tiene 138 minutos con barras negociadas: no se rellenaron los minutos ausentes.
- El reloj del replay avanzó al cierre de cada minuto. Las barras del día completo no se usaron como calentamiento. Se reconstruyeron Swing, Intraday y Support con `MarketBotAssembly`, en memoria y sin conectar el bus operativo, Redis ni PostgreSQL.
- La auditoría no ejecutó órdenes, publicó eventos operativos, modificó reglas ni cambió la configuración.

## 1. El bloqueo efectivo fue Swing

Las 26 observaciones regulares de Swing fueron `BULLISH / WATCH`, con `short_structure_gate_passed=false`. Esa dirección no implica compra LONG: su veredicto fue WATCH.

La política requiere simultáneamente pérdida de SMA20, precio al menos 2% debajo de SMA50 y pérdida del AVWAP del breakout, además del contexto de failed breakout. Los niveles publicados fueron:

| Referencia de Swing | Precio |
|---|---:|
| SMA20 diaria | 60,7715 |
| SMA50 diaria | 62,6586 |
| Umbral SMA50 menos 2%, calculado para diagnosticar el gate | 61,405428 |
| AVWAP del pivot low | 60,6690 |
| Soporte estructural de Swing | 57,2700 |

El mínimo intradía fue **61,02**, todavía por encima de la SMA20. Por eso no hubo pérdida diaria suficiente ni `short_setup_id` habilitado. En las dos señales maduras Intraday, los precios 61,78 y 61,65 estaban también por encima del umbral de SMA50 menos 2%.

Referencia de implementación: `app/leveraged_thesis_engine/v13.py:86–99` exige el consenso; `app/swing_engine/v14.py:104–138` calcula la tesis diaria. La configuración está en `configs/rules/swing/3.5.0.yaml`.

## 2. Las dos señales Intraday sí existieron

La recomputación histórica reproduce exactamente las dos ventanas, los precios y las rutas de confirmación observadas en vivo.

| Vela, hora NY | Disponible al cerrar | Publicada en vivo, NY | Ruta | Precio referencia | Invalidación SHORT | Objetivo |
|---|---|---|---|---:|---:|---:|
| 13:06 | 13:07 | 13:07:07,861 | IMPULSE_BREAKDOWN | 61,7800 | 61,9345 | 61,5483 |
| 13:07 | 13:08 | 13:08:04,446 | DISPLACEMENT | 61,6500 | 61,8041 | 61,4188 |

Ambas fueron `BEARISH / FAVORABLE`, score 70, confianza 0,70, calidad fuerte y `short_mature_confirmation_gate_passed=true`. La primera tuvo RVOL 3,2693 y momentum de cinco minutos −0,4832%; la segunda, RVOL 4,1679 y momentum −0,6446%.

No son dos alertas SHORT operativas ni dos compras. Son dos observaciones consecutivas de timing Intraday dentro del mismo movimiento, rechazadas por los requisitos adicionales de Leveraged Thesis.

### Seguimiento hipotético de sus niveles

Para evitar usar información anterior a la publicación, se siguieron solamente velas completas que comienzan después de cada publicación. También se muestra una entrada hipotética en la apertura del minuto siguiente, conservando los niveles originales. No hay garantía de fill ni descuento de spread, comisiones o slippage.

| Señal | Apertura del siguiente minuto | Primer nivel alcanzado | Minuto NY | Resultado bruto hipotético desde esa apertura |
|---|---:|---|---|---:|
| Vela 13:06 | 61,635 a las 13:08 | Objetivo 61,5483 | 13:08 | +0,1407% |
| Vela 13:07 | 61,720 a las 13:09 | Stop 61,8041 | 13:09 | −0,1363% |

Las velas utilizadas no tocaron stop y objetivo a la vez. Estos son escenarios independientes para revisar timing, no una estrategia de dos operaciones ni un resultado atribuible a ASTN. La caída posterior no convierte la segunda señal en ganadora: su stop ocurrió primero.

## 3. Soporte habría impuesto otro bloqueo

El assessment disponible, calculado a las 09:23:30 NY con datos diarios hasta el jueves, estaba en `FIRST_TOUCH`, zona **60,9368–64,1232**, invalidación **57,9126**. Esa zona cubrió todo el rango de ASTS del viernes, **61,02–63,215**.

La función `_nearest_support` de Leveraged Thesis devuelve el propio precio cuando éste está dentro de la zona. Por ello la distancia al soporte es **0 ATR** en ambas señales; la política exige al menos **0,50 ATR de espacio** o una ruptura de **0,25 ATR**. Incluso suprimiendo hipotéticamente el requisito de Swing, el guard de soporte seguiría fallando.

Este es un bloqueo potencial comprobado, no un WATCH bloqueado efectivamente emitido: el motor retorna antes al fallar Swing. Además, el soporte cercano de Fibonacci en 60,3795 deja aproximadamente 0,35 y 0,32 ATR en esas señales, también por debajo de 0,50 ATR; quitar únicamente el efecto de estar dentro de la zona no basta.

## 4. ASTN tiene además un problema de vigencia de cotizaciones

En los **496 estados regulares de ASTN**, solamente **9** tenían simultáneamente quote con edad efectiva de hasta dos segundos y spread de hasta 35 bps al persistirse. Ninguno produjo evidencia completa de entrada en la reproducción de `advance_short`.

El desfase entre `OrderFlowState.occurred_at` y el timestamp de almacenamiento fue, sobre los 501 estados del viernes incluyendo los no regulares:

- Mediana: **3,014 segundos**.
- Percentil 90: **3,298 segundos**.
- Máximo: **8,320 segundos**.

Leveraged Thesis suma ese tiempo a `quote_age_ms`, y admite un máximo de **2 segundos**. Que el estado diga `quote_fresh` en origen no garantiza que siga siendo ejecutable al consumirlo. El tiempo de almacenamiento es una aproximación optimista a la disponibilidad del consumidor, no una medición exacta de su procesamiento. El desfase por sí solo no localiza la causa: puede incluir demoras anteriores al bus, procesamiento o diferencias de reloj.

A las 13:07:07 NY, el último estado de ASTN carecía de bid/ask y reportaba `quote_timestamp_ahead_of_trade`. En la segunda señal, el último estado era de las 13:07:19 NY, casi 45 segundos anterior a la publicación, aunque su quote había sido fresco en origen.

Como sensibilidad, al eliminar artificialmente todo desfase de recepción aparecieron tres evidencias de subida de precio de ASTN a las 10:54 NY. Ya tenían más de dos horas de antigüedad en las señales de las 13:07–13:08, muy por encima de la retención válida de 30 minutos. No rescatan esas entradas.

La composición de Order Flow actualiza el quote al recibirlo, pero publica estados a partir de trades/correcciones/cancelaciones. En un ETF con actividad intermitente esto merece una revisión específica de vigencia y publicación. No debe confundirse con una autorización para ignorar cotizaciones antiguas ni atribuir todo el desfase a NATS.

## Conclusión y decisión sugerida

Hay una diferencia entre el objetivo de capturar caídas intradía y la política vigente: ésta exige una ruptura de tesis diaria más protección de soporte diario. El viernes el timing Intraday funcionó; la ruta compuesta lo rechazó de manera consistente.

La revisión aconseja estudiar una ruta SHORT táctica separada si se busca operar esos movimientos, manteniendo explícita su diferencia con la tesis diaria y validándola en varias ruedas. Cambiar sólo el umbral Swing, el guard de soporte o la tolerancia temporal no resuelve los tres problemas a la vez. La vigencia de ASTN requiere diagnóstico independiente antes de considerar aptas sus cotizaciones para entrada.

**Resultado operativo de la rueda: sin madurez de compra SHORT de ASTS ni de ASTN.**

## Artefactos y verificación

Los datos, replay y diagnóstico están en `.runtime/asts-friday-20260925/`:

- `events.jsonl`: eventos originales con secuencia y hora de persistencia.
- `bars.json`: OHLCV histórico y calentamiento.
- `backtest.json`: análisis recomputados y cero decisiones SHORT.
- `findings.json`: señales, gates, estados de ASTN y seguimiento hipotético.
- `backtest.py`, `evaluate.py`, `summarize.py`: reproducción y diagnóstico. La extracción está en `.runtime/audit_asts_friday.py`.

Comandos de reproducción desde la raíz:

```powershell
uv run python .runtime/asts-friday-20260925/backtest.py
uv run python .runtime/asts-friday-20260925/evaluate.py
```

Validación del repositorio: `uv run ruff check .` correcto; `uv run pyright` fue bloqueado por Windows Application Control y se ejecutó el equivalente `uv run python -m pyright`, con cero errores y advertencias; `uv run pytest`: **1.845 passed, 11 skipped**, cobertura **79,88%**. Se conservaron las modificaciones preexistentes del usuario.
