# JACK — Backtest Swing Trade, 28/08 al 04/09/2026

MarketBot 7.50.0 · Swing Trade 1.7.0 · reglas 1.4.0 · Entry Opportunity 11.0.0.
Datos históricos Alpaca SIP, ajuste split. Replay de 1.619 barras de un minuto, con historial previo para inicialización. Transporte y persistencia en memoria, sin órdenes ni uso de NATS/PostgreSQL operativos.

## Compras emitidas

Fechas originales; horarios de Nueva York (ET, UTC−4).

| Fecha | Emisión | Vela confirmada | Madurez | Entrada USD | Stop operativo USD | Riesgo inicial | Confirmación |
|---|---|---|---|---:|---:|---:|---|
| 02/09/2026 | 12:17 | 12:15 | ST3 | 16,1600 | 15,7960 | 2,2525% | Ruptura + retesteo |
| 04/09/2026 | 12:15 | 12:15 | ST3 | 15,7800 | 15,5635 | 1,3720% | Ruptura + retesteo |

No hubo compras confirmadas el 28/08, 31/08, 01/09 ni 03/09. No se emitió ST4. Los estados ST1/ST2 y checkpoints de seguimiento no son compras.

## Seguimiento de las operaciones simuladas

- **02/09:** referencia recuperada 16,05. Salida a las 16:00 del mismo día en 15,87 por dos cierres debajo del nivel recuperado (`rebound_acceptance_failed`). Resultado de la operación: **−1,7946%**. El stop operativo era 15,7960 y la invalidación diaria 15,1299: la salida se produjo antes de ambos. La distancia original a la invalidación diaria habría sido aproximadamente 6,37%.
- **04/09:** referencia recuperada 15,68. La operación quedó **abierta** al finalizar el replay; último precio del registro de operaciones 16,15, equivalente a **+2,3447% no realizado** desde 15,78. Stop conservado en 15,5635. No se supone una venta al cierre.
- En ambas entradas, MACD 4H figuró como `INSUFFICIENT_HISTORY` y dirección `UNKNOWN`. No bloqueó las compras ni provocó la salida.

## Lectura y límites del resultado

Este resultado verifica las señales de la versión actual, no demuestra rentabilidad futura ni calibra los parámetros con una muestra de dos operaciones. El precio de entrada del simulador es el cierre de confirmación; no incorpora comisión ni deslizamiento. En la primera señal, publicación 12:17 y cierre de evidencia 12:15 son timestamps distintos.

El backtester desplaza internamente los timestamps siete días para su reloj de simulación; este resumen los devuelve a las fechas de origen. El JSON bruto conserva las fechas simuladas 04/09–11/09. El backtester general inicializa el contexto diario antes de la ventana y reproduce barras de un minuto; esta corrida no equivale a reinicializar el motor con nuevas barras diarias cada jornada. El calentamiento 4H del backtester es más corto que el del proceso operativo; por eso su MACD queda sin suficiente historia. La última evaluación Swing Trade disponible corresponde al 04/09 a las 15:45; el seguimiento de operaciones alcanza la última barra de un minuto (15:59). No se inventa una evaluación de las 16:00 que el replay no emitió.

Para P/L de operaciones se usaron las legs/checkpoints ST3 y el evento de salida explícita. `swing_trade_outcomes` incluye ST2 y mide excursiones hasta el final de la ventana, incluso después de una salida; esos valores no deben interpretarse como pérdidas realizadas de las compras.

JSON bruto: `.runtime/backtests/jack-20260828-20260904-v1750.json`.
