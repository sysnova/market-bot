# Mi ticker en vivo

La sección **Mi ticker** de Opportunities sigue un símbolo independiente de los filtros de la
tabla. Recupera el último evento retenido por subject en JetStream y continúa escuchando cambios
de análisis, assessments, Order Flow, setups, Entry Watcher, señales y alertas. Incluye el estado
disponible de Opportunities desde el libro PostgreSQL y el contexto global de Market Rotation.
No modifica el universo, las reglas ni la ejecución de órdenes.

La sección **SHORT**, accesible desde la navegación superior, usa el mismo ticker y los mismos
eventos en vivo. Separa la estructura SHORT de Swing, la confirmación madura de Intraday y la
última alerta `BEARISH_CONSENSUS` con razón `short_entry_confirmed` publicada por Alert Engine.
No calcula una confirmación combinando gates. Una alerta antigua se identifica como histórica;
sus niveles no implican que la entrada siga disponible. La ausencia de alertas tampoco representa
un diagnóstico del estado del motor. El aviso de extensión EMA y la configuración de su bloqueo
se explican por separado. Los detalles incluyen rutas alternativas, no una lista de condiciones
que deban pasar todas. Los datos insuficientes y las desconexiones no muestran gates verdes.
Un ETF inverso conserva su propia evidencia; no hereda automáticamente los gates del subyacente.

La web separa **Evaluaciones recientes** de **Evidencia anterior y alertas**. Distingue
`assessed_at`/`generated_at` (fecha de evaluación) de la fecha de sus datos base: una evaluación
nueva no vuelve recientes sus datos antiguos ni habilita sus gates. `generated_at` también es
reconocido cuando es la única fecha publicada, como en Rotación y el assessment de Gamma.
Una evaluación anterior del mismo dato no puede sobrescribir otra más nueva por llegar tarde.
Las tesis apalancadas se filtran por `underlying_symbol` e `instrument_symbol` y muestran ambos
campos; sus subjects están nombrados por el subyacente. «NATS conectado» describe el transporte,
y el listado de motores sin evento distingue configuración activa, programada y bajo demanda.

Cada assessment muestra motor, versión, fecha del dato, razones, gates publicados y el payload
completo desplegable. Los indicadores booleanos con polaridad explícita se proyectan a semáforos:
verde cumple, rojo no cumple o riesgo activo, amarillo antiguo, gris sin evidencia. Los campos
adversos como `structure_broken_confirmed` invierten la polaridad. No se reconstruyen reglas ni
umbrales a partir de scores. Los motores que no publican gates lo indican expresamente.

La antigüedad se calcula desde `as_of`, nunca desde la recepción o el replay. El límite visual
de Swing es 32 minutos: su vela de 15 minutos se fecha desde la apertura, se espera el próximo
cierre de 15 minutos y se permiten dos minutos de entrega. Para los demás assessments se mantienen
15 minutos. Es una política de presentación, no un TTL de trading ni una confirmación de entrada.
Una expiración explícita anterior prevalece. Un dato sin fecha, futuro o sin conexión al bus no
puede presentarse verde. Los assessments diarios pueden estar antiguos aunque el mercado esté
cerrado. Los motores ausentes se enumeran con su modo operativo; ausencia no equivale a veto.

**Solicitar análisis** invoca el analyzer existente por ticker y deja que sus resultados lleguen
por eventos. Es una ejecución puntual, con la cobertura y los skips del analyzer (no recalcula
todos los motores existentes). El seguimiento en vivo depende de que los motores correspondientes
estén operando. Los datos persistidos se mantienen visibles si el análisis falla.

**Enviar contexto y preguntar** es la única acción de Mi ticker que llama a OpenAI. Usa el mismo
reviewer, credencial y `MARKETBOT_THESIS_REVIEW_MODEL` de Failure Lab. El lanzador Linux carga la
credencial compartida de `stock-analyzer/apps/alert-runner/.env` cuando corresponde, como antes.
El servidor congela el contexto actual completo, agrega la pregunta y hasta seis intercambios
anteriores del mismo ticker, y realiza una llamada Responses sin herramientas y con `store=false`.
Si el contexto supera 1 MB, rechaza la solicitud sin recortarlo silenciosamente. Las respuestas
incluyen ticker, modelo y hora de ese contexto. Se guardan con el snapshot en
`.runtime/ticker-reviews/ticker-YYYY-MM-DD.ndjson`.

El snapshot incluye `short_context`, con referencias a la estructura Swing, el timing Intraday
y la última confirmación SHORT recibida. Documenta la ruta de Alert 3.9/3.10 sólo cuando la
versión configurada es conocida; para otras versiones no presume requisitos. No calcula una
señal ni reconstruye las decisiones de Alert. Conserva fechas y vigencia por fuente, incluida
la degradación a `UNKNOWN` al perder la conexión, y declara que no contiene el recorrido
completo del día. La configuración delimita el universo SHORT en 3.10; los estados de
4HGERI, apalancados, Portfolio Flow y Order Flow no se presentan como vetos de esa ruta.

En Swing 14/15, `short_thesis_broken` es un nombre heredado para la ruptura de la tesis LONG
que habilita la estructura SHORT. La web conserva el nombre original y su valor, pero muestra
su significado y polaridad en la tesis SHORT. No cambia la polaridad de otros campos `broken`,
otros motores o versiones desconocidas. Estas aclaraciones también forman parte de las
instrucciones enviadas a GPT; una condición estructural no sustituye la alerta confirmada.

Cambiar de ticker cancela las tareas y las suscripciones anteriores y reinicia el contexto de
conversación. Las solicitudes llevan correlación y una respuesta tardía nunca se dibuja sobre
otro ticker. Una reconexión restablece la suscripción, pero no reenvía preguntas a GPT.

Si el replay tarda, la sesión conserva sus suscripciones y muestra `SYNCING` hasta completar
la carga. El heartbeat comprueba los consumidores del ticker y reintenta suscripciones
fallidas; una consulta exitosa de Rotación no acredita la conexión del ticker. SHORT muestra
la interrupción o sincronización dentro de su sección y el progreso publicado del historial
de un minuto (por ejemplo, 3 de 30 velas). Si el Dashboard arrancó sin bus, lo identifica como
un servicio que requiere restablecimiento, sin prometer reintentos de esa conexión global.

Diagnóstico ASTS del 15/9/2026: el runtime 7.71.0 publicaba Intraday 8.0.0 de la rueda actual,
pero una pestaña podía quedar sin suscripciones tras agotar el timeout inicial, conservando
historial parcial del 10/9. La prueba contra NATS real con el código corregido de Windows
pasó de `SYNCING` a `NATS_REPLAY_AND_LIVE` y recuperó ASTS del 15/9 a las 13:36 UTC,
con `insufficient_1m_history:7/30`. Esto valida recepción, no una entrada SHORT confirmada.
La corrección está en `app/integration/ticker_web_session.py` y `static/ticker.js`.
Su instalación en WSL requiere autorización según el AGENTS.md del workspace y reiniciar
únicamente el Dashboard. No se modificó el checkout ni se reinició el runtime WSL durante
esta validación.

**Detener análisis**, disponible en Mi ticker y en SHORT, cancela las tareas de análisis y
consulta de esa sesión, elimina sus suscripciones y limpia su contexto. La web borra el ticker
recordado para que una reconexión o recarga no vuelva a seguirlo automáticamente. También se
puede detener la vista sin conexión; sólo «Seguir ticker» inicia nuevamente el seguimiento.
El alcance es la pestaña actual: no desactiva los motores compartidos ni las alertas generales
del monitor, no cambia la watchlist y no cierra posiciones. El servidor confirma la detención
con `ticker_stopped`; las respuestas tardías de la selección detenida se descartan.

Validación enfocada:

```sh
uv run pytest app/opportunity_dashboard/tests app/integration/tests/test_ticker_web_session.py --no-cov
node --test app/opportunity_dashboard/tests/*.test.cjs
```


### Recuperación tras reiniciar y velas 4H

El seguimiento recupera los resultados analíticos retenidos del ticker y conserva
el mayor `as_of` por motor/tipo/horizonte. Un bootstrap atrasado publicado después
no debe sustituir una lectura más nueva al abrir otra sesión. Los demás subjects
mantienen su recuperación del último evento por subject.

4HGERI separa la evaluación (`assessed_at`, límite visual de 15 minutos) del inicio
de su última vela 4H cerrada (`occurred_at`). La vela conserva vigencia hasta el
siguiente cierre RTH más 2 minutos; una evaluación detenida o una expiración
explícita siguen marcando la evidencia antigua. Se usan los segmentos habituales
del runtime, 09:30–13:30 y 13:30–16:00 de Nueva York, saltando fines de semana.
Esta política no incorpora un calendario de feriados ni cierres anticipados.


En el arranque, Market History también comprueba la vigencia del tramo intradía:
una caché horaria no basta para 1/5/15 minutos. Si ni su última vela ni su última
consulta son recientes para ese intervalo (+2 minutos), descarga sólo el tramo
faltante. El loader excluye las velas REST de 1/5/15 minutos y 1 hora que aún no
han cerrado. Swing reconstruye los intervalos completos con su historial de
1 minuto y conserva el intervalo en formación para continuar el stream.
