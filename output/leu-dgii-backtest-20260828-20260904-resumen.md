# Backtest LEU y DGII — 28/08 al 04/09/2026

MarketBot 7.50.0, Swing Trade 1.7.0, reglas 1.4.0. Datos Alpaca SIP, ajuste split; 3.107 barras de un minuto reproducidas. Ejecución aislada en memoria, sin órdenes ni modificaciones de PostgreSQL.

## Resultado

| Símbolo | Compras Swing Trade ST3/ST4 | Madurez máxima | Evaluaciones |
|---|---:|---|---:|
| LEU | 0 | ST2 | 156 |
| DGII | 0 | ST1 | 155 |

No hubo errores de validación del impulso LONG. Las evaluaciones incluyen la inicialización.

## LEU: la nueva confirmación sí impide repetir la entrada histórica en este replay

La compra histórica registrada fue el 28/08 a las 15:16 ET, en 174,75, con salida el 01/09 a las 09:31 ET en 166,2903 (−4,84%).

En la vela del 28/08 a las 15:15 ET, el replay usa el mismo precio de 174,75 y la misma invalidación estructural de 166,2903. Tiene soporte/Fibonacci compatibles y cumple el rechazo anterior, VWAP de barra y RVOL 1,4191. La lógica previa alcanza ST3, pero la confirmación nueva permanece WATCHING y la madurez final es ST2: no existe ruptura y aceptación completas.

El 02/09 a las 15:15 ET ocurre otro caso en 172,30: la lógica previa reúne condiciones ST3, mientras la nueva registra BREAKOUT sobre referencia 171,31 sin aceptación posterior suficiente. La madurez final sigue en ST2. Durante toda la ventana hubo cuatro observaciones BREAKOUT y ninguna compra.

## DGII: no vuelve a comprar, pero hay un filtro estructural previo

En PostgreSQL existen dos compras históricas del 01/09: 72,51 (salida −0,74%) y 71,97 (salida −3,70%). En las velas de las 10:45 y 11:15 ET del replay aparecen esos mismos precios y se cumplen rechazo, VWAP de barra y RVOL (1,2486 y 2,2327 respectivamente).

Sin embargo, el contexto diario del replay no muestra confluencia: Fibonacci 70,1664–73,3501 frente a banda de soporte 67,2701–68,5899. DGII queda en ST1 y no registra ruptura aceptada. Por ello, no podemos atribuir su ausencia de compras exclusivamente a la mejora del rebote.

## Otra familia sí emitió una señal

LEU recibió una compra CORE_RECOVERY L2 el 04/09 a las 15:15 ET, en 171,9899, con invalidación 161,8057. Quedó abierta al finalizar el replay, con marca 173,76. Es una señal de otra familia, no Swing Trade, y no está gobernada por el límite de riesgo de Swing Trade 1.7.0.

## Límites

El backtester general conserva el contexto diario inicial durante la ventana: no equivale a actualizar la geometría con nuevas barras diarias cada jornada. Los resultados de LEU muestran un bloqueo concreto por la nueva confirmación bajo los mismos datos disponibles en esta corrida; no constituyen una prueba general de rentabilidad. En DGII, el contexto inicial diferente de producción impide atribuir el bloqueo únicamente a las reglas nuevas.

El JSON bruto desplaza los timestamps siete días por el reloj de simulación; todas las fechas de este resumen son las originales y los horarios son de Nueva York (ET). Los precios de entrada del simulador corresponden al cierre de evidencia, que puede preceder a su publicación. No se incorporan comisiones ni deslizamiento.

JSON completo: `.runtime/backtests/leu-dgii-20260828-20260904-v1750.json`.
