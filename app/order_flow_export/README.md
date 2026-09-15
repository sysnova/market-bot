# WebSocket de Order Flow

Exporta los eventos del Engine existente. No conecta a Alpaca, no calcula indicadores
y no crea órdenes. El exportador es un adaptador de transporte; la selección del
Engine y su política sigue perteneciendo a `MarketBotAssembly`.

## Arranque manual en Windows

Desde el checkout de MarketBot, con NATS y el proceso Order Flow funcionando:

```powershell
$env:MARKETBOT_ORDER_FLOW_WS_TOKEN = "REEMPLAZAR_POR_UN_TOKEN_PRIVADO"
uv run marketbot serve order-flow
```

Usar un token aleatorio privado. No pasarlo por argumentos ni incluirlo en URLs.
También se admiten estas variables en el `.env` local (no versionado).

| Variable | Valor por defecto |
| --- | --- |
| `MARKETBOT_ORDER_FLOW_WS_HOST` | `127.0.0.1` |
| `MARKETBOT_ORDER_FLOW_WS_PORT` | `8766` |
| `MARKETBOT_ORDER_FLOW_WS_TOKEN` | Obligatorio; sin valor por defecto |
| `MARKETBOT_NATS_URL` | Configuración NATS habitual del proyecto |
| `MARKETBOT_DEFINITION_PATH` | Definición MarketBot vigente |

Endpoint: `ws://127.0.0.1:8766/ws/order-flow`.
El servidor escribe la lista de símbolos habilitados al arrancar. Se obtiene de
la política seleccionada; una lista vacía no habilita todos los símbolos.
Cambiar la política requiere reiniciar el exportador para actualizar esa lista.
No inicia automáticamente el Engine. Si hace falta, se arranca por separado con
`uv run marketbot engine order-flow`. Detener el exportador con Ctrl+C.

Ngrok se configura por separado, apuntando al puerto 8766 desde un agente que
pueda alcanzar ese listener. El cliente externo usa
`wss://<dominio-ngrok>/ws/order-flow`, conservando el encabezado Authorization.
El listener predeterminado sólo acepta conexiones del equipo local; el enlace
externo TLS lo termina ngrok.

## Cliente de prueba para Windows

Desde la carpeta del proyecto:

```powershell
.\test-order-flow.cmd ASTS NBIS
```

Si no configuraste `MARKETBOT_ORDER_FLOW_WS_TOKEN` en esa terminal, solicita el
token con entrada oculta. Usar el mismo token del servidor. Sin argumentos
también solicita los tickers. No guarda el token en archivos.

Muestra conexión, confirmación de suscripción, estado, precio, CVD, confianza y
volumen comprador/vendedor con delta por ventana. Distingue los cambios de estado.
La confirmación dice "Esperando novedades": puede no haber eventos si el Engine
está detenido o el ticker no recibe actualizaciones.

```powershell
.\test-order-flow.cmd ASTS --json
.\test-order-flow.cmd ASTS --once
.\test-order-flow.cmd ASTS --url wss://<dominio-ngrok>/ws/order-flow
```

`--json` muestra los mensajes completos; `--once` termina tras el primer evento
de estado o transición (no tras la confirmación). Ctrl+C termina la escucha.

## Cliente de prueba para macOS

El lanzador `test-order-flow.command` usa el mismo cliente, con un entorno uv
independiente del proyecto:

```bash
bash test-order-flow.command ASTS NBIS --url wss://<dominio-ngrok>/ws/order-flow
```

También puede distribuirse junto a `example_client.py` sin el resto del repo.
Ver [instrucciones para macOS](README_MACOS.md). Solicita el token con entrada
oculta y envía automáticamente la suscripción.

### Invocación Python directa

En otra terminal, configurar el mismo token y ejecutar:

```powershell
uv run python -m app.order_flow_export.example_client ASTS NBIS
```

Para acceso externo:

```powershell
uv run python -m app.order_flow_export.example_client ASTS --url wss://<dominio-ngrok>/ws/order-flow
```

El ejemplo usa la dependencia `websockets` del proyecto, imprime los eventos y
se vuelve a suscribir después de una reconexión. Termina ante token, ruta o
suscripción inválidos. Un navegador con WebSocket nativo no permite configurar
este encabezado; este contrato está orientado a clientes de backend/Python.

## Protocolo

Handshake HTTP con `Authorization: Bearer <token>`:

- `401`: token ausente, incorrecto o encabezado duplicado.
- `404`: ruta distinta de `/ws/order-flow` (sin query string).
- `503`: NATS o suscripciones todavía no disponibles.

El cliente no recibe datos hasta enviar un mensaje JSON de texto:

```json
{"action":"subscribe","symbols":["ASTS","NBIS"]}
```

Respuesta:

```json
{"type":"subscribed","symbols":["ASTS","NBIS"],"delivery":"live"}
```

Los símbolos se normalizan a mayúsculas, se recortan espacios y se eliminan
duplicados. Una solicitud válida reemplaza la selección anterior. Los mensajes
ya encolados antes del cambio pueden llegar antes de su confirmación; después
de la confirmación sólo llegan eventos de la nueva selección.

Una lista vacía, un ticker no habilitado, JSON inválido, mensajes binarios,
campos adicionales o una acción desconocida producen:

```json
{"type":"error","code":"invalid_subscription","description":"Send action=subscribe and a nonempty list of enabled symbols."}
```

La suscripción anterior se conserva ante un error. El máximo de entrada es
16 KiB por mensaje; excederlo cierra la conexión con `1009`.

## Datos recibidos

Los eventos son los `EventEnvelope` originales, sin conversión de decimales ni
un envoltorio extra. `event_type` distingue los datos; `type` distingue los
mensajes de control. Se valida el payload y su correspondencia con el ticker
antes de distribuirlo. Eventos inválidos se descartan con un log sin payload.

| Campo del envelope | Significado |
| --- | --- |
| `event_id` | UUIDv7 del evento; coincide con state_id o transition_id |
| `event_type` | `order-flow.state.assessed` o `order-flow.state.transitioned` |
| `schema_version` | Versión del envelope, actualmente `1.0.0` |
| `occurred_at` | Timestamp de referencia UTC, no hora de recepción |
| `source` | `order-flow` |
| `subject` | Ticker |
| `payload` | Estado o transición descritos abajo |
| `causation_id` | UUID del evento causante o null |
| `trace_id`, `correlation_id` | Identificadores opcionales o null |
| `market_session` | Sesión opcional; la composición actual no la informa |
| `attributes` | Lista de metadatos name/value; normalmente vacía |

### Payload de estado: OrderFlowState

Decimales viajan como strings (`"123.4500"`), fechas como ISO 8601 UTC y UUID
como strings. `null` indica ausencia de evidencia o que el campo no aplica.
Las puntuaciones de confianza/calidad no son probabilidades de rentabilidad.

| Campo | Tipo y significado |
| --- | --- |
| `state_id` | UUIDv7 de la evaluación |
| `symbol` | String, ticker |
| `occurred_at` | Timestamp UTC del cálculo |
| `engine_version` | Versión del Engine, distinta de schema_version |
| `state` | Estado confirmado; estabilizado en Engine 1.2 |
| `pulse_state` | Lectura instantánea o null |
| `candidate_state` | Estado pendiente de confirmación o null |
| `candidate_samples` | Entero, evaluaciones del candidato; cero sin candidato |
| `state_stable_since` | Timestamp UTC desde la estabilización o null |
| `current_price` | Decimal, último precio de operación mantenido |
| `mid_price` | Decimal o null, midpoint del último quote; puede estar antiguo |
| `bid_price`, `ask_price` | Decimales o null, evidencia fresca de cotización |
| `spread_bps` | Decimal o null, (ask-bid)/midpoint × 10000 |
| `cumulative_delta` | Decimal, buy-sell desde el inicio/reinicio del estado del ticker |
| `confidence` | Decimal entre 0 y 1, confianza de la evaluación |
| `data_quality` | Decimal entre 0 y 1, calidad de datos |
| `quote_age_ms` | Decimal o null, antigüedad de la quote en milisegundos |
| `quote_fresh` | Booleano, cumple la antigüedad máxima de la política |
| `unknown_trade_ratio` | Decimal entre 0 y 1; proporción de volumen desconocido en 300 s |
| `windows` | Cinco ventanas móviles, en orden 1/5/15/60/300 segundos |
| `reasons` | Lista de motivos de evaluación |
| `source_event_ids` | UUID vinculados a la actualización; no todos los trades acumulados |
| `context_hash` | Huella `sha256:<64 hex>` del contexto; no firma de autenticidad |

Con la versión 1.2, bid/ask/spread quedan en null cuando la quote no está fresca.
El CVD refleja lo procesado desde el inicio o último reset; no acredita haber
recuperado una sesión completa tras un arranque o interrupción.

### Cada ventana: OrderFlowWindow

| Campo | Tipo y significado |
| --- | --- |
| `window_seconds` | Entero: 1, 5, 15, 60 o 300 |
| `trade_count` | Entero, operaciones en la ventana |
| `buy_volume`, `sell_volume` | Decimales, acciones clasificadas por agresor |
| `neutral_volume` | Decimal, acciones con clasificación neutral |
| `unknown_volume` | Decimal, acciones sin lado clasificable |
| `delta` | Decimal, buy_volume - sell_volume |
| `volume_velocity` | Decimal, volumen total / duración de ventana, acciones/segundo |
| `large_buy_volume`, `large_sell_volume` | Decimales, acciones en trades que alcanzan el umbral grande configurado |
| `price_change_bps` | Decimal, cambio primer/último trade × 10000; cero con menos de dos trades |

100 bps = 1 %. `total_volume` es una propiedad calculada del modelo Python y
no viaja en el bus: sumar buy + sell + neutral + unknown. La clasificación del
agresor es estimada. Las inferencias L1 no equivalen a profundidad del libro.

### Estados posibles

`NEUTRAL`, `BUY_PRESSURE`, `SELL_PRESSURE`, `SELLER_EXHAUSTION`,
`BUYER_EXHAUSTION`, `BUY_ABSORPTION`, `SELL_ABSORPTION`,
`BULLISH_DIVERGENCE`, `BEARISH_DIVERGENCE`.

Representan neutralidad, presión compradora/vendedora, agotamiento vendedor/
comprador, absorción de sesgo comprador/vendedor y divergencia alcista/bajista,
respectivamente, según la política del Engine. Son los valores posibles de
state, pulse_state y candidate_state; estos últimos dos también admiten null.

### Payload de cambio: OrderFlowTransition

| Campo | Significado |
| --- | --- |
| `transition_id` | UUIDv7 del cambio |
| `state_id` | UUIDv7 de la evaluación asociada |
| `symbol`, `occurred_at`, `engine_version` | Ticker, tiempo UTC y versión |
| `previous_state` | Estado anterior o null |
| `state` | Nuevo estado |
| `confidence`, `current_price` | Decimales: confianza 0–1 y precio |
| `reasons`, `context_hash` | Motivos y huella del contexto |

## Continuidad

- Se escucha NATS Core, sobre subjects de estado y transición de los tickers
  habilitados. No crea consumidores durables ni escribe en JetStream.
- Sólo eventos nuevos desde la suscripción. Sin snapshot, replay, cursor ni
  garantía de entrega durante cortes. Conservar event_id para deduplicación si
  el productor repite eventos. No asumir un orden temporal total entre subjects.
- Order Flow limita la publicación de estados a una separación mínima de un
  segundo cuando recibe actualizaciones; las transiciones viajan por separado.
- Ping/pong cada 20 s, timeout 20 s. No confundir conexión viva con Engine activo:
  un Engine detenido puede dejar la conexión abierta sin datos nuevos.
- Cola por cliente de 256 mensajes. Al llenarse se cierra ese cliente con 1013,
  `slow_consumer`; no se bloquea NATS ni a los demás clientes.
- Caída de NATS: cierre 1013, `upstream_disconnected`. Se vuelve a aceptar al
  restablecer conexión y suscripciones. Cada cliente debe volver a suscribirse.
- Apagado del servicio: cierre 1001, `server_shutdown`.
- No exporta trades/quotes individuales, presiones personalizadas, ganancia ni stops.

## Pruebas

```powershell
uv run pytest app/order_flow_export/tests app/integration/tests/test_order_flow_websocket.py --no-cov
```

El caso extremo a extremo usa `ORDER_FLOW_WS_TEST_NATS_URL`, que debe apuntar a
un NATS **aislado para pruebas**: publica eventos sintéticos de ASTS. Sin esa
variable se omite sólo ese caso. Las pruebas de handshake usan loopback y no
requieren credenciales reales ni ngrok.
