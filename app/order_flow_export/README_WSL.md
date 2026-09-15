# Iniciar el WebSocket desde WSL

En el checkout Linux actualizado y con el entorno `.venv` preparado:

```bash
cd ~/Projects/market-bot
bash start-order-flow-websocket.sh
```

El servicio tiene un archivo privado propio: **`app/order_flow_export/.env`**.
Guardar alli una sola vez el token del WebSocket:

```dotenv
MARKETBOT_ORDER_FLOW_WS_TOKEN=TU_TOKEN_DEL_WEBSOCKET
MARKETBOT_ORDER_FLOW_WS_HOST=127.0.0.1
MARKETBOT_ORDER_FLOW_WS_PORT=8766
```

El script y `marketbot serve order-flow` lo leen automaticamente en cada
arranque. Los demas procesos de MarketBot no leen este archivo. No usar el
authtoken de ngrok. El archivo esta excluido de Git; `.env.example` es una
plantilla sin credenciales.

Prioridad: variables del proceso > `.env` propio del WebSocket > `.env` general
(compatibilidad e infraestructura) > valores predeterminados. El puerto
predeterminado es **8766**. Solo si no hay token en ninguna fuente, el script
lo solicita con entrada oculta. El script no guarda lo ingresado por el prompt.

Se ejecuta en primer plano: mantener la terminal abierta. Ctrl+C detiene el
WebSocket. NATS y el Engine Order Flow deben estar funcionando previamente.
El script no instala dependencias, no actualiza el checkout, no arranca otros
engines ni modifica configuracion. Desactiva la escritura de bytecode Python.

En otra terminal:

```bash
ngrok http 8766
```

Si el puerto esta ocupado, el script informa el conflicto y no inicia otro
proceso. Para la instancia temporal creada anteriormente con systemd:

```bash
systemctl --user status marketbot-order-flow-websocket
```

Si queres reemplazar esa instancia por la ejecucion manual del script:

```bash
systemctl --user stop marketbot-order-flow-websocket
bash start-order-flow-websocket.sh
```

## Ejecutar el script de Windows usando el runtime de WSL

Tambien se puede usar el archivo del checkout Windows sin copiarlo ni actualizar
el checkout Linux, indicando donde esta el runtime:

```bash
MARKETBOT_PROJECT_ROOT="$HOME/Projects/market-bot" \
  bash /mnt/c/Users/lgonz/Projects/market-bot/start-order-flow-websocket.sh
```

## Cambiar el puerto

```bash
MARKETBOT_ORDER_FLOW_WS_PORT=8767 bash start-order-flow-websocket.sh
```

En ese caso, apuntar ngrok al mismo puerto. Para consultar la ayuda:

```bash
bash start-order-flow-websocket.sh --help
```
