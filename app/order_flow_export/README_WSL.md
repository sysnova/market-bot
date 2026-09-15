# Iniciar el WebSocket desde WSL

En el checkout Linux actualizado y con el entorno `.venv` preparado:

```bash
cd ~/Projects/market-bot
bash start-order-flow-websocket.sh
```

El script lee la configuracion existente de MarketBot (`.env` y variables de
entorno). El puerto predeterminado es **8766**. Si falta el token del WebSocket,
lo pide con entrada oculta: pegarlo y pulsar Enter, aunque no se vean caracteres.
No incluye tokens ni los guarda. No usar el authtoken de ngrok.

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
