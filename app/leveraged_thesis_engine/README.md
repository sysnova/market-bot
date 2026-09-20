# Leveraged Thesis engine

Motor intraday de avisos y oportunidades paper, sin ejecución de broker. Observa el subyacente y selecciona el
instrumento que se compra para expresar la dirección:

| Subyacente | Tesis alcista | Tesis bajista |
|---|---|---|
| ASTS | ASTX (`LONG_2X`) | ASTN (`INVERSE_2X`) |
| NBIS | NBIS (`LONG_1X`) | NBIZ (`INVERSE_2X`) |

## Versión 1.1: entrada diferida del inverso

Las alertas reales `SHORT CONFIRMED` (`BEARISH_CONSENSUS` con
`short_entry_confirmed`) de ASTS/NBIS crean una intención pendiente para ASTN/NBIZ.
Se conserva la invalidación publicada y la identidad de la señal en Redis bajo
`pending-short:v1:`. La limpieza de ventanas de engines no elimina estas claves.
No se vuelve a exigir el score o trigger Intraday ni flujo vendedor simultáneo
del subyacente. Un SHORT pendiente vence al cierre regular (16:00 Nueva York)
o se cancela al alcanzar el subyacente la invalidación original; esa cancelación
no se revierte porque el precio vuelva a bajar.

El instrumento puede confirmar por flujo comprador válido (confianza >= 0,65 y
calidad >= 0,70) **o** por subida de su midpoint respecto de hace tres minutos.
La ventana usa un midpoint por segundo y un ancla de tres minutos con tolerancia
de cinco segundos; sin esa cobertura todavía puede confirmar por flujo.
Ambas vías necesitan bid/ask actuales (edad efectiva <= 2 segundos), spread <=
35 bps y precio reciente del subyacente para comprobar la invalidación (<= 60 s).
No se interpreta flujo neutral como comprador ni se exige confianza de flujo
para la vía de precio. La ventana acumula también antes del SHORT, por lo que
no impone esperar tres minutos desde la alerta si ya hay evidencia.

Las decisiones y su publicación se persisten por separado. La recuperación y
los reintentos reutilizan IDs distintos para assessment, transición y entrada;
no vuelven a registrar una compra consumida. `BUY_CONFIRMED` alimenta el mismo
registro paper de Opportunities, sin enviar órdenes al broker. Las tesis LONG
conservan las reglas anteriores. Configuración nueva: MarketBot 7.72.0,
LeveragedThesis 1.1.0; 7.71.0 / 1.0.0 queda disponible para rollback.

## Versión 1.0 y reglas LONG

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


### Confirmacion del instrumento anterior al SHORT (1.1)

ASTN/NBIZ guardan en Redis su ultima condicion de entrada cumplida incluso sin
SHORT pendiente: flujo comprador valido o subida del midpoint en tres minutos,
con quote vigente y spread admisible. Se conserva una sola evidencia por par,
con fecha, precio de referencia, motivo e ID del flujo; no un historial creciente.

Al confirmar el SHORT del subyacente se admite esa evidencia durante un maximo
de 30 minutos, siempre de la misma rueda regular. La extension posterior del ETF
no descarta la entrada ni exige repetir el flujo comprador. Se mantiene el control
de invalidacion del subyacente y se necesita bid/ask vigente y spread admisible;
la compra paper usa el ask actual, nunca el precio historico de la evidencia.
La evidencia sobrevive al reinicio junto con el estado pendiente en Redis, fuera
de las vistas descartables. La caducidad logica de 30 minutos se verifica al evaluar,
aunque la clave de estado tenga un TTL tecnico mayor para conservar la deduplicacion.


## Version 1.2: ASTX LONG por recuperacion diaria/Swing de ASTS

MarketBot `7.73.0` selecciona Leveraged Thesis `1.2.0`. ASTX sustituye el camino
alcista Intraday anterior de ASTS: recibe EntrySignal nativos de ASTS de SwingTrade
ST3/ST4 o Core Swing L2 (tambien L3/L4 cuando incluyen el horizonte SWING).
ST1/ST2, L1 y simples observaciones no arman la compra. No se recalculan barras
ni se requiere que ambas familias confirmen a la vez.

La intencion LONG conserva la invalidacion publicada por la primera confirmacion
y vence al cierre de la rueda. ASTS al tocar o caer por debajo de ese nivel cancela
la tesis. La confirmacion de ASTX puede llegar antes o despues; se admite flujo
comprador o subida en tres minutos, incluida evidencia de hasta 30 minutos de la
misma rueda guardada en `pending-long:v1:ASTS`. Una extension posterior no obliga
a repetir esa confirmacion; siempre se exige quote actual y spread admisible.

Se conserva una unica evidencia y una ventana acotada por instrumento. Redis
retiene esta informacion al reiniciar, separada de ASTN, y no se limpia con las
vistas de engines. La publicacion persistida reintenta los mismos IDs y recibir
las dos familias para la tesis activa no duplica la entrada. El assessment referencia
el EntrySignal original sin inventar un score Intraday para SwingTrade.

La compra paper se registra sobre ASTX con exposicion LONG_2X, ask actual y el
riesgo propio del ETF ya utilizado por Leveraged Thesis (stop 3%, objetivo 2R,
seguimiento Intraday con cierre de rueda). El origen diario/Swing no convierte
esta operacion en una posicion multirrueda. ASTN/NBIZ conservan la regla SHORT
1.1; NBIS alcista conserva su camino previo LONG_1X.

## Version 1.3: propietario unico de la decision SHORT

MarketBot `7.77.0` mueve la confirmacion SHORT de ASTS/NBIS a Leveraged Thesis
`1.3.0`. Swing e Intraday siguen produciendo evidencia independiente; este engine
es el unico que combina ambos gates, valida los niveles y aplica la proteccion de
soporte. Exige timing Intraday de hasta dos minutos y falla cerrado si no dispone
de soporte estructural y ATR. Cerca del soporte publica un WATCH bloqueado; con
espacio de al menos 0,50 ATR, o una ruptura de 0,25 ATR, publica
`short_entry_confirmed`.

La alerta sale primero al bus durable para que Opportunities registre el paper
SHORT del subyacente; el mismo evento arma despues el seguimiento de ASTN/NBIZ.
Order Flow del instrumento decide despues si existe una compra valida del ETF
inverso; no participa en la confirmacion SHORT del subyacente. La identidad de la
decision es estable por setup para que replays y reinicios no dupliquen entradas.
Alert `3.12.0` conserva las demas alertas, pero ya no puede confirmar SHORT.
