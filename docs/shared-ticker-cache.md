# Caché de datos por ticker entre procesos

El runtime distribuido inicia `ticker-cache` antes de los consumidores. Este proceso
es el único dueño de los historiales retenidos y de los contextos analíticos
compartidos. Cada Engine conserva su proceso, su estrategia y su posición en el
flujo de eventos. PostgreSQL sigue siendo la persistencia; NATS sigue transportando
eventos. La caché se reconstruye al iniciar y no modifica esas fuentes.

## Inventario de entradas

Revisión del catálogo y de las composiciones de la definición 7.71.0. Los límites
son los existentes; compartir almacenamiento no amplía ventanas de las estrategias.
Los símbolos incluyen el universo efectivo de cada Engine: watchlist, posiciones,
subyacentes y referencias macro cuando corresponda.

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
| Market Rotation | 100 diarias por símbolo/proxy/benchmark en cada ciclo | Lee PostgreSQL, calcula y libera el lote antes de esperar; no retiene otra caché histórica |
| Options Gamma | Cadena de opciones, quotes/open interest y precio | Datos específicos consultados por ciclo, sin historial OHLCV duplicado |
| News Intelligence | Noticias y oportunidades vigentes | Datos específicos del proveedor/repositorio, sin historial OHLCV duplicado |
| Dilution SEC | Filings y hechos SEC por solicitud | Documentos específicos; no duplica historiales de precios |
| Peter Lynch | Datos fundamentales/SEC y valuación por solicitud | Datos específicos; no duplica historiales de precios |

`MarketBarStore` conserva su API. En el runtime distribuido envía lotes de hasta
256 barras y consulta ventanas al servicio. Los procesos no mantienen `_series`
con objetos OHLCV; sólo existe un buffer acotado de escritura y snapshots
transitorios para el cálculo. Los lotes de bootstrap se liberan antes de esperar
eventos. Una ejecución aislada sin `--shared-cache` conserva el adaptador local,
útil para tests, backtests y diagnóstico.

## Consistencia y memoria

Cada payload inmutable idéntico tiene una sola representación JSON en RAM,
identificada por SHA-256. Las vistas mantienen referencias ordenadas por símbolo,
timeframe y timestamp. El límite, la sesión filtrada y el progreso de cada
consumidor siguen independientes. Una corrección sólo reemplaza la referencia
del consumidor que ya la recibió. Los demás no ven una barra futura ni pierden
su versión anterior. Feed, fuente, OHLCV y estado final forman parte del payload;
las agregaciones diferentes no se confunden aunque tengan el mismo timestamp.

Los contextos de análisis usan el mismo almacenamiento con vistas tipadas. Se
preservan las claves enum de los mapas por horizonte y las lecturas devuelven
snapshots. Compartir almacenamiento no autoriza a sustituir el estado de una
oportunidad, tesis o libro de microestructura por el de otro Engine.

Las referencias vencen cuando el dueño termina o deja de renovar su lease durante
180 segundos. El heartbeat corre cada 30 segundos en un hilo propio. Las ventanas
descartan referencias al superar su capacidad; el último consumidor que libera
un payload libera su almacenamiento. Long/Swing/Intraday también descartan las
series retiradas en sus eventos de cambio de universo. Las composiciones que
cargan un universo fijo conservan ese alcance hasta reiniciarse, igual que antes.

No es memoria de objetos Python accesible sin copia: los cálculos reciben sólo
sus snapshots mediante JSON por loopback. Esa transferencia tiene costo de CPU
y latencia y se mide junto con el ahorro. La RAM total de NATS, PostgreSQL, los
intérpretes y los estados particulares no desaparece por centralizar la caché.

## Operación

Los lanzadores Windows y Linux usan el plan canónico, que pasa
`--shared-cache <runtime-root>/ticker-cache.endpoint.json` a los consumidores.
El endpoint sólo escucha en `127.0.0.1`, usa un token aleatorio local y no emplea
pickle. El archivo de readiness no contiene el token. El contrato wire es JSON
interno del mismo checkout; no cambia los eventos públicos de NATS.

Si el servicio falla, las consultas fallan: no se crea silenciosamente otra
caché privada ni se continúa con un historial vacío. Reiniciar el runtime completo
reconstruye las vistas y sus dependencias desde PostgreSQL/NATS. Un Engine lanzado
manualmente debe recibir el mismo `--shared-cache` para participar.

Diagnóstico sin imprimir credenciales:

```powershell
uv run marketbot serve ticker-cache-stats --endpoint-path .runtime/ticker-cache.endpoint.json
uv run python scripts/benchmark_shared_ticker_cache.py
```

Las métricas distinguen payloads únicos, referencias, bytes únicos y bytes
evitados. Estos últimos son bytes serializados, no una estimación de toda la RAM.
El benchmark compara 206 símbolos, 260 barras y cuatro consumidores en hijos
limpios, incluyendo el servidor de caché en la medición RSS del caso compartido.
Es sintético y no equivale a una medición del bot completo en producción.

Medición en Windows/Python 3.14: 421,55 MiB retenidos con las cuatro cachés locales
frente a 57,77 MiB incluyendo el servicio compartido (86,3% menos). La carga tardó
3,09 s frente a 8,43 s; consultar 824 ventanas tardó 0,003 s frente a 2,674 s.
El costo medio de una ventana remota de 260 barras fue aproximadamente 3,2 ms.

La agregación diaria de minutos también usa estadísticas OHLCV incrementales:
retiene como máximo la primera y la última barra del día, en lugar de otras
390 barras por símbolo en Long y Swing. Mantiene los mismos cálculos de VWAP,
volumen y conteo de operaciones y las mismas condiciones de cierre de sesión.
