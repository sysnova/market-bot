# Recuperación LONG y ruptura SHORT — MarketBot 7.51.0

Implementación en el checkout de Windows. Sin despliegue WSL, cambios en la
watchlist, publicación operativa de este replay ni órdenes al broker.

## Versiones y comportamiento

- GERI 1.9.0, reglas 1.3.0: `recovery.py` evalúa recuperación LONG y
  `short_structure.py` evalúa ruptura SHORT. Ya no se invierte automáticamente
  el lado de la cadena para decidir qué operación táctica buscar.
- Recuperación: piso confirmado después de una caída local, techo fijado antes
  de la ruptura, cierre sobre techo + 0.10 ATR y al menos una vela 15m posterior
  consecutiva que acepte el nivel y conserve un mínimo superior al del breakout.
- CT0/CT1 observan; CT2 registra aceptación; CT3 añade cierre 4H; CT4 continuación.
  La madurez observada y la elegibilidad de entrada son campos distintos.
- Entrada: stop fijado bajo el mínimo del tramo ruptura/aceptación con 0.10 ATR
  de margen; riesgo máximo 4%, R/R estrictamente mayor que 1.5, vigencia de cinco
  sesiones y límite de extensión. Objetivo: primer obstáculo conocido por encima.
- Fibonacci local 4H: anclas confirmadas, retroceso 50–61.8% y soporte vigente
  coincidente con el precio actual. Prioridad ordinal 1 frente a 0 sin confluencia;
  disponible en métricas y motivos. No altera sizing ni permite eludir riesgo.
- SHORT: soporte diario perdido, precio bajo EMA8/SMA21/SMA50 diarias y VWAP
  anclado a pivote 4H, confirmación bajista 15m y RVOL >= 1.5 frente a cinco
  sesiones anteriores a la misma hora. Objetivo inferior y stop superior a entrada.
  Sin objetivo direccional válido no se aprueba. SHORT sigue siendo analítico.
- Entry Opportunity 12.0.0: CT0/CT1 no abren posiciones ni checkpoints de P/L.
  CT2–CT4 vuelven a validar geometría y R/R al ingresar. Dashboard muestra las
  referencias tempranas separadas de compras.
- Se completa la última vela de 15m sin esperar el siguiente día. Un día agregado
  exige las 26 velas RTH; huecos o datos fuera de orden no fabrican confirmación.

## Replay causal del 28/08 al 04/09/2026

Datos Alpaca SIP, ajuste split. Arranque aislado con hasta 600 barras RTH de
15m previas al 28/08 y 100 barras diarias previas. Se evalúa al cierre de cada
15m, con actualización diaria únicamente tras sesión completa. Sin NATS ni
PostgreSQL operativo. No reconstruye un estado persistido de producción.

| Ticker | Velas 15m evaluadas | Recuperación observada | Entradas CT2/CT3/CT4 emitidas |
|---|---:|---|---:|
| HUT | 156 | CT2 y CT3, con riesgo no elegible | 0 |
| JACK | 156 | Hasta CT1 | 0 |
| LEU | 156 | CT2, con riesgo no elegible | 0 |
| DGII | 155 | Hasta CT1; una vela ausente | 0 |

En ninguno de los cuatro símbolos se confirmó un SHORT con todos los gates.
Esto no acredita rentabilidad ni estima la tasa de falsas entradas; las pruebas
unitarias adicionales cubren entradas válidas, falsas rupturas y fallos del piso.

### HUT: diferencia frente a la versión anterior

El piso del 01/09 en 75.3096 queda confirmado al completarse el bloque de la tarde.
La referencia local de recuperación es 79.99. El 02/09 a las **14:30 ET** se
observa CT2 tras ruptura y aceptación:

- Precio de evaluación: **80.7550**.
- Stop del tramo: **78.40614816**.
- Primer objetivo: **81.55**, máximo diario del 24/08, ya conocido y confirmado.
- R/R: **0.33846**, inferior a 1.5; no se emite entrada.
- Al cierre del 02/09 se observa CT3, manteniendo el rechazo de riesgo.

La versión nueva sí reconoce la recuperación. No salta la resistencia 81.55
para usar 86.89/116.72 y presentar artificialmente un R/R más favorable.

## Evidencia reproducible

- Ensamblado: `uv run marketbot assembly` selecciona 7.51.0 / GERI 1.9.0 /
  Opportunity 12.0.0 en este checkout.
- Replay: `uv run python .runtime/backtests/replay_recovery.py`.
- Datos descargados: `.runtime/backtests/recovery-history.json`.
- Traza y señales: `.runtime/backtests/recovery-7.51.0.json`.
- Pruebas: `.runtime/recovery-pytest.log`; incluye integración runtime → señal →
  oportunidad y conserva pruebas de las versiones previas.

El ensamblado 7.50.0 y las implementaciones previas permanecen disponibles.
Los agregadores de esta versión siguen usando la sesión regular 09:30–16:00;
no sintetizan días completos en sesiones reducidas.
