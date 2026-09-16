# Redis compartido por ticker

El runtime distribuido usa Redis como propietario de ventanas OHLCV y contextos
analíticos. Redis vive en un contenedor independiente: detener MarketBot no lo
apaga ni borra sus datos. PostgreSQL sigue siendo la fuente durable de históricos
y estado de negocio; NATS transporta los eventos.

## Arranque y actualización

1. `ticker-cache` comprueba Redis y limpia únicamente las ventanas, índices y
   contextos propios de engines antes de publicar su endpoint y readiness.
   Conserva las barras canónicas `history:*`, sus metadatos de cobertura y los
   snapshots de eventos. Las intenciones SHORT pendientes o ya consumidas de
   `pending-short:v1:` también sobreviven a la limpieza para evitar pérdidas y duplicados. Al liberar referencias elimina sólo los payloads que
   ya no usa ninguna ventana, sin borrar barras compartidas. La limpieza funciona
   incluso con Redis en maxmemory. Todos los engines esperan este paso; conectar
   o reiniciar un engine individual no lo ejecuta. PostgreSQL y NATS no se modifican.
2. Antes de publicar readiness, `market-history-v1` combina los requisitos de los
   Engines activos y prepara las ventanas del universo en Redis. Lee PostgreSQL
   por ticker, nunca como un lote de toda la watchlist.
3. Cada ventana guarda cobertura (cantidad, última barra, fecha de descarga y
   capacidad). Las ventanas extendidas se cargan sólo cuando el pedido incluye
   premarket; la precarga general de 15m/1h usa únicamente RTH. El supervisor
   retira las antiguas ventanas extendidas sobrantes al iniciar, incluso si Redis
   ya alcanzó su límite de memoria. Si la cobertura no cambió, los siguientes arranques reutilizan
   Redis sin volver a materializar ese histórico desde PostgreSQL. Se mantiene
   la comprobación de frescura del servicio histórico y la recuperación de huecos.
4. Los Engines esperan al servicio histórico y reciben un iterable Redis: durante
   bootstrap sólo se materializa una ventana por ticker. Los ordenamientos de
   Swing, GERI, SwingTrade y recuperación también quedan acotados por ticker.
   Los símbolos adicionales de cada Engine se preparan centralmente antes de
   responder su solicitud. Rotation usa el mismo recorrido en sus ciclos.
5. El ingreso del stream actualiza Redis antes de publicar barras nuevas o
   corregidas en NATS. Las actualizaciones periódicas del servicio histórico
   también refrescan Redis. Los Engines guardan sus agregaciones y contextos ahí.

No se omiten las validaciones de frescura, sesiones, barras completas o estrategias
al reutilizar una caché. Reiniciar con datos viejos exige actualizar lo faltante.

## Ventanas y estado

| Engine | Datos solicitados | Almacenamiento / estado propio |
| --- | --- | --- |
| Long | 260 diarias, 220 semanales; 500 minutos para reconstruir el día | Barras en caché; intervalo diario en formación local |
| Swing | 120 diarias, 160 de 15m; 500 minutos de arranque; Support y Order Flow Support | Barras y evaluaciones compartidas; agregadores locales |
| Intraday | 500 de 1m, 100 de 5m; premarket y RTH | Barras compartidas; agregador respeta sesiones |
| 4HGERI | 600 de 15m, 100 diarias; deriva 4h y 1h; Swing, Support, Order Flow Support, madurez | Barras y evaluaciones compartidas; cursores y madurez locales |
| SwingTrade | 120 diarias, 180 de 15m o 1200 para momentum según versión; 4h; 120 días de momentum; GERI y Support | Barras y contextos compartidos, incluyendo momentum; deduplicación de emisiones local |
| Support Confirmation | 520 diarias, 420 semanales, 500 de 1h; evaluación previa | Barras y evaluación compartidas |
| Volume Structure | 420 semanales; resultado anterior | Barras y resultado compartidos |
| Elliott Wave | 400 diarias, 500 de 1h, posiciones positivas | Barras compartidas; cursor de evaluación local |
| Patreon Caps | 260 diarias, 220 semanales, 220 de 1h, 160 de 15m, 500 de 1m; Long/Swing/Intraday; referencias macro | Barras y análisis compartidos; watches/estado durable conservan su repositorio |
| Signal Fusion | Support, Wave, Patreon, análisis por horizonte, evaluación anterior | Contextos compartidos; sin historial OHLCV adicional |
| Alert | Último análisis por ticker/horizonte | Contextos compartidos; cooldowns y decisiones propias locales/durables |
| Entry Watcher | Último análisis por ticker/horizonte, tesis activa | Análisis compartidos; tesis y transiciones continúan en PostgreSQL |
| Entry Recovery | Último Swing/Intraday, oportunidad invalidada | Análisis compartidos; estado de recuperación propio |
| Entry Opportunity | Análisis/señales/transiciones, barras de recuperación | Estado canónico ya consultado en PostgreSQL; lote de recuperación liberado al terminar |
| Order Flow | Quotes/trades en ventanas temporales, Support | Support compartido; libro y estadísticas derivados pertenecen a este Engine |
| Leveraged Thesis | Intraday, Order Flow, Support, evaluación previa | Contextos compartidos; pares/reglas son configuración |
| Portfolio Flow | Quotes/trades y ventana de flujo por posición | Estado de microestructura específico, sin copia del historial OHLCV |
| Long Portfolio | Análisis Long y asignaciones/posiciones | Sin historial OHLCV local; calificaciones y alertas son estado propio |
| Market Rotation | 100 diarias por símbolo/proxy/benchmark en cada ciclo | Lee ventanas Redis por ticker; conserva sólo las entradas reducidas del cálculo |
| Options Gamma | Cadena de opciones, quotes/open interest y precio | Datos específicos consultados por ciclo, sin historial OHLCV duplicado |
| News Intelligence | Noticias y oportunidades vigentes | Datos específicos del proveedor/repositorio, sin historial OHLCV duplicado |
| Dilution SEC | Filings y hechos SEC por solicitud | Documentos específicos; no duplica historiales de precios |
| Peter Lynch | Datos fundamentales/SEC y valuación por solicitud | Datos específicos; no duplica historiales de precios |


Cada payload idéntico tiene una sola copia en Redis, identificada por SHA-256.
Los índices por consumidor conservan capacidad, sesión y posición en los eventos;
no almacenan otra copia del payload. Una corrección reemplaza la referencia del
mismo timestamp. Los cambios de referencia, reemplazos y recortes son atómicos
mediante Lua. Esto preserva la separación entre barras de proveedores y barras
agregadas con reglas distintas, y evita que un consumidor vea anticipadamente
una corrección que todavía no procesó.

Los contextos tipados tienen identificadores explícitos por Engine y mapa; sus
valores y grupos se recuperan de Redis después de reiniciar. No se mezcla, por
ejemplo, el último resultado Swing con Intraday por usar el mismo ticker.
Las vistas transitorias tienen heartbeat de 30 s y lease de 180 s; liberar un
cliente libera sus referencias transitorias, pero conserva históricos y contextos
persistentes. Las ventanas tienen límites de filas. Redis no es un reemplazo de
los repositorios durables de oportunidades, tesis ni órdenes.

Los cálculos aún necesitan objetos temporales Python, y cada proceso conserva
sus reglas, cursores y agregadores. Este cambio elimina los lotes históricos de
toda la watchlist en cada Engine; no pretende eliminar la memoria base de Python
ni resolver el heap de NATS.

## Operación

Crear/iniciar Redis desde el checkout que se va a operar:

```powershell
docker run -d --name marketbot-redis --restart unless-stopped --publish 127.0.0.1:6379:6379 --memory 2560m --memory-swap 5g --mount type=volume,source=market-bot_marketbot-redis,target=/data --health-cmd "redis-cli ping" --health-interval 10s --health-timeout 3s --health-retries 3 redis:8.2-alpine redis-server --appendonly yes --appendfsync everysec --maxmemory 2gb --maxmemory-policy noeviction
```

Configuración: `MARKETBOT_REDIS_URL=redis://127.0.0.1:6379/0` en el entorno o
`.env`. El endpoint que reciben los Engines está restringido al usuario local;
las métricas no imprimen el URL. Redis está publicado sólo en loopback.

El contenedor usa AOF (`appendfsync everysec`) y un volumen Docker: también puede
recuperar la caché tras reiniciar el contenedor. AOF puede perder el último segundo
ante un fallo abrupto; las comprobaciones de cobertura y frescura siguen siendo
obligatorias. El contenedor es independiente, sin proyecto Compose. El volumen
conserva su nombre original para reutilizar los datos existentes. Para reiniciar,
usar `docker restart marketbot-redis`; no eliminar el volumen.

El límite inicial de Redis es 1 GiB, con política `noeviction` y límite del
contenedor de 1536 MiB. Son límites operativos iniciales, no una medición del
universo completo. Si Redis no está disponible o no puede aceptar una escritura,
el acceso falla: nunca se crea silenciosamente una caché privada ni se descartan
ventanas necesarias mediante LRU. Ajustar el límite sólo después de medir.

```powershell
uv run marketbot serve ticker-cache-stats --endpoint-path .runtime/ticker-cache.endpoint.json
docker exec marketbot-redis redis-cli INFO memory
```

La ejecución aislada sin `--shared-cache` conserva el adaptador local para tests
y backtests. El servidor HTTP anterior queda únicamente como harness de pruebas;
el runtime operativo usa el cliente Redis directamente.

## Verificación

Las pruebas cubren deduplicación entre clientes, correcciones, límites, conservación
de históricos y contextos al reconectar, carga única desde PostgreSQL, invalidación
por cambios de cobertura, selección de barras completas y resultados existentes
de Long y SwingTrade con Redis. El smoke del CLI contra Redis real se habilita con
`MARKETBOT_TEST_REDIS_URL` apuntando a una base aislada.

El benchmark anterior de `scripts/benchmark_shared_ticker_cache.py` corresponde
al prototipo HTTP retirado y no mide este diseño ni el consumo completo de WSL.
La RAM y el tiempo del bot completo deben medirse tras desplegar este cambio.
# Current analytical events and startup restoration

The dashboard no longer replays seven days of analyses to choose a current
result. The event bus restores at most one envelope per matching subject,
reconciles each exact subject with JetStream, and uses Redis to preserve the
newest analytical evaluation across process restarts. Redis current-event
keys use `marketbot:cache:v2:events:<prefix>:<stream>:<subject>` and are independent
of ephemeral engine leases. Bars remain in the existing canonical history views.

Live delivery is established before restoration. Callbacks wait for restoration,
then acknowledge only after successful handling. Cache publication follows the
JetStream acknowledgement, and restoration reconciles the last retained event
to repair an interrupted cache write. Current-state consumers use `NEW`, not
`LAST_PER_SUBJECT`; historical consumers explicitly requesting `replay_all` keep
their original semantics. The dashboard no longer requests `replay_all`.

The shared history iterator counts bars while bootstrap consumes them. The
startup logging path no longer makes a separate complete Redis read and JSON
decode just to count bars. This change does not skip stateful engine calculation
or assume that a persisted context is a complete, compatible engine checkpoint.

Measured on 16 September 2026 against the same operational stream: restoring
1,081 analysis subjects via `LAST_PER_SUBJECT` took 9.48 s and grew NATS HeapAlloc
from 16 MB to 6.23 GB. Direct current-state restoration took 1.01 s and grew heap
from 6.5 MB to 118 MB. A diagnostic AAPL dashboard session restored 14 assessments
in 104 ms with empty snapshot keys and 71 ms on a second session with persisted
keys. These are component measurements, not a claim about full-process startup
peak RAM. No production stream history was deleted for these tests.
