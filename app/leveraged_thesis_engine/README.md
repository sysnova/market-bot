# Leveraged Thesis engine

Motor intraday de avisos y oportunidades paper, sin ejecución de broker. Observa el subyacente y selecciona el
instrumento que se compra para expresar la dirección:

| Subyacente | Tesis alcista | Tesis bajista |
|---|---|---|
| ASTS | ASTX (`LONG_2X`) | ASTN (`INVERSE_2X`) |
| NBIS | NBIS (`LONG_1X`) | NBIZ (`INVERSE_2X`) |

La secuencia causal es `OBSERVING → EARLY_FLOW → STRUCTURE_ARMED →
BUY_CONFIRMED`. `EARLY_FLOW` puede aparecer antes de un trigger de vela cuando
el SIP L1 del subyacente ya es direccional y el soporte no contradice la tesis.
`BUY_CONFIRMED` requiere mercado regular, estructura Intraday alineada, order
flow SIP fresco en el subyacente, quote ejecutable y order flow comprador en el
instrumento elegido.

Support Confirmation aporta las zonas clave cercanas al spot. Para LONG, un
primer toque o zona en vigilancia sólo deja la tesis armada: hace falta reacción,
reclaim o estructura con score suficiente para confirmar. Para SHORT, una zona
cercana y no invalidada bloquea incluso el aviso temprano; la tesis se libera
cuando no hay soporte cercano o el spot rompe la invalidación. Assessment ausente,
vencido o viejo queda `OBSERVING`, nunca se interpreta como evidencia bajista.

La composición no consume barras, trades ni quotes crudas. Se suscribe únicamente a los
`AnalysisResult` Intraday y `SupportAssessment` exactos de ASTS/NBIS y a los `OrderFlowState` de
los cinco símbolos exactos. Bid, ask y spread llegan versionados dentro de Order Flow 1.1. Support
Confirmation incorpora ASTS/NBIS a su universo aunque no estén temporalmente en la watchlist.

Un flujo vendedor en el instrumento, feed degradado, evidencia vencida o spread
ancho produce `BLOCKED`. Las evaluaciones caducan en minutos y nunca se
convierten en órdenes, cantidades ni posiciones reales.

`EARLY_FLOW` publica solamente un `LocalAlert`: avisa el armado temprano, pero no
abre una compra. `BUY_CONFIRMED` publica un `EntrySignal` de familia
`LEVERAGED_THESIS`; el monitor de compras lo muestra y Entry Opportunity abre y
trackea el instrumento como cualquier otra compra paper. La oportunidad usa el ask
del instrumento como entrada, bid/ask como zona, stop inicial 3% debajo del ask y
primer target 2R. El leg es `INTRADAY`, por lo que se marca con barras del propio ETF
y se cierra al final de la rueda si antes no alcanzó stop o target. Los cinco símbolos
fijos permanecen elegibles durante la reconciliación para que la oportunidad no se
cierre sólo por estar fuera de la watchlist rotativa.

Como ASTX, ASTN y NBIZ son ETF
de objetivo diario, la señal no proyecta una relación 2× multirrueda ni usa el
precio del subyacente como stop del ETF.
