# HUT — Countertrend 1.9.0, 28/08–04/09/2026

Replay ejecutado nuevamente el 08/09/2026 en Windows con MarketBotAssembly
7.51.0, GERI 1.9.0 y reglas 1.3.0. Resultado: **0 entradas CT2/CT3/CT4**.

## Método

156 velas completas de 15 minutos, 26 por cada una de las seis sesiones.
Histórico Alpaca SIP con ajuste split, reutilizado desde el archivo local
`recovery-history.json`. Calentamiento con hasta 600 velas RTH de 15 minutos y
100 diarias anteriores al inicio. Evaluación secuencial al cierre de cada vela;
el motor recibe únicamente barras completadas. Publicador aislado en memoria,
sin órdenes ni publicación de este replay en NATS/PostgreSQL operativo.
Se reconstruye un estado inicial desde histórico, no el estado persistido real.

## Evolución

| Fecha / hora Nueva York | Evidencia | Compra |
|---|---|---|
| 28/08 | Candidato anterior expirado | No |
| 31/08 | Al cierre aparece preparación CT1 | No |
| 01/09 | Nuevo piso 75,3096; referencia de recuperación 79,99 | No |
| 02/09 14:30 | Ruptura y aceptación CT2 a 80,755 | No: R/R insuficiente |
| 02/09 16:00 | Confirmación 4H, CT3 a 80,62 | No: R/R insuficiente |
| 03/09 09:45 | La primera vela ya alcanzó el objetivo 81,55; candidato agotado | No |
| 04/09 | Sin nueva entrada; cierre 93,50 | No |

## Bloqueo de la primera aceptación CT2

- Precio: 80,755.
- Stop: 78,40614816; riesgo 2,34885184 por acción, aproximadamente 2,91%.
- Objetivo: 81,55; beneficio potencial 0,795 por acción, aproximadamente 0,98%.
- R/R: 0,33846 frente al mínimo estricto superior a 1,5.
- Motivo emitido: `insufficient_reward_risk`.
- Confluencia Fibonacci estructural: falsa; ninguna de las 156 evaluaciones
  cumplió conjuntamente precio en banda Fibonacci y soporte coincidente.

El nuevo modelo detecta la recuperación LONG, pero su selección del primer
obstáculo conocido como objetivo bloquea esta entrada. Alcanzar CT2 o CT3 no
anula el filtro de riesgo. Las señales CT0/CT1 son referencias de seguimiento,
no compras ni posiciones con P/L.

## Resultado y límites

No hubo operaciones que permitan medir rentabilidad, aciertos, MAE o MFE de la
estrategia. El precio pasó de 80,755 en la primera aceptación a 93,50 al cierre
del 04/09 (+15,78%). Esa comparación es retrospectiva: no representa el P/L del
modelo y supone mantener hasta el cierre ignorando su objetivo de 81,55.

El punto que merece una evaluación separada es la clasificación y vigencia de
las resistencias que limitan el objetivo. Este replay no modifica reglas ni
optimiza parámetros para provocar una compra en HUT.

## Evidencia

- Traza de 156 evaluaciones: `hut-countertrend-1.9-backtest-20260828-20260904.csv`.
- Replay reproducible: `uv run --no-sync python .runtime/backtests/replay_recovery.py`.
- Datos: `.runtime/backtests/recovery-history.json`.
- Resultado completo: `.runtime/backtests/recovery-7.51.0.json`.
