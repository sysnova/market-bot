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

Cambiar de ticker cancela las tareas y las suscripciones anteriores y reinicia el contexto de
conversación. Las solicitudes llevan correlación y una respuesta tardía nunca se dibuja sobre
otro ticker. Una reconexión restablece la suscripción, pero no reenvía preguntas a GPT.

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
