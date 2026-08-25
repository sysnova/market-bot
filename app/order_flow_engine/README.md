# Order Flow engine

`OrderFlowEngine` es una capa de inteligencia intraday determinista. Consume contratos tipados
`MarketQuote`, `MarketTrade`, `MarketTradeCorrection` y `MarketTradeCancel`; no conoce cuentas,
posiciones, órdenes ni APIs de ejecución.

## Clasificación causal

Cada trade se clasifica con la mejor evidencia disponible en ese instante:

1. ejecución en ask/bid;
2. posición respecto del midpoint cuando el quote está fresco;
3. tick rule si no hay quote fresco;
4. `UNKNOWN` cuando tampoco existe un precio anterior.

Una ejecución exactamente en el midpoint se mantiene `NEUTRAL`. El engine rechaza trades fuera
de orden para evitar recalcular el pasado con información futura. Correcciones y cancelaciones
revierten Delta/CVD de forma idempotente; una corrección reutiliza el quote causal guardado con el
print original.

## Estado publicado

Cada actualización contiene las ventanas canónicas de 1, 5, 15, 60 y 300 segundos, calculadas
exclusivamente con `Decimal`:

- volumen buy/sell/neutral/unknown y Delta;
- velocidad de volumen;
- large trades por lado;
- variación de precio en basis points;
- CVD acumulado de la sesión;
- frescura del quote, proporción desconocida, calidad y confianza;
- desde 1.1, bid, ask y spread en bps dentro del propio `OrderFlowState`.

Los estados compactos son presión compradora/vendedora, exhaustion, absorption, divergencia y
neutral. Sólo se genera `OrderFlowTransition` cuando cambia el estado. `reset_symbol()` debe ser
invocado por la composición al iniciar una nueva rueda para reiniciar el CVD intraday.

## Límites operativos

- La política 1.1 limita el hot path a `ASTS`, `ASTX`, `ASTN`, `NBIS` y `NBIZ`.
  La composición crea veinte subscriptions NATS exactas (quote/trade/correction/cancel) y no
  escucha wildcards. El rollback 1.0 conserva el comportamiento anterior.
- Los consumidores reciben estados sólo de esos cinco símbolos porque Order Flow no calcula ni
  publica el resto de la watchlist.
- Es inteligencia operativa: publica evidencia para Leveraged Thesis y consumidores estructurales,
  pero no envía órdenes.
- Usa SIP top-of-book; absorption y divergence son inferencias L1, no profundidad de libro.
- Los últimos cinco minutos se conservan para ventanas; el ledger mínimo de contribuciones se
  mantiene durante la sesión para poder revertir correcciones tardías.
- Los thresholds son una política inyectable. La composición y la definición MarketBot deciden
  qué implementación/política queda activa.
