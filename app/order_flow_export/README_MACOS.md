# Cliente de prueba para macOS

Requiere macOS 13 o posterior y uv. Funciona como cliente externo: no necesita
MarketBot, NATS ni Alpaca instalados en la Mac.

## Preparacion

1. Extraer el ZIP completo. Mantener juntos `test-order-flow.command` y
   `example_client.py`.
2. Instalar uv si no esta instalado. Con Homebrew:

   ```bash
   brew install uv
   ```

   Sin Homebrew, consultar la instalacion oficial:
   https://docs.astral.sh/uv/getting-started/installation/

3. Abrir Terminal en la carpeta extraida y ejecutar, reemplazando el dominio:

   ```bash
   bash test-order-flow.command ASTS NBIS --url wss://TU_DOMINIO_NGROK/ws/order-flow
   ```

La primera ejecucion descarga Python 3.14 y websockets si hacen falta; requiere
Internet. El entorno se gestiona con uv, sin instalar todo el proyecto.

## Token y suscripcion

El programa pide el token de nuestro WebSocket con entrada oculta. Pegar el
token y presionar Enter; es normal que no se vean caracteres. No usar el
authtoken de ngrok. Tambien admite `MARKETBOT_ORDER_FLOW_WS_TOKEN` por entorno.
Este paquete no incluye ni guarda tokens.

Envia automaticamente el encabezado Authorization y la suscripcion JSON a los
tickers indicados. Debe mostrar "Conectado y autenticado" y "Suscripto".
Luego imprime estado, precio, CVD, confianza y buy/sell/delta por ventana.
La ausencia de eventos nuevos no equivale a un error de conexion.

Tickers permitidos por la politica actual: ASTS, ASTX, ASTN, NBIS y NBIZ.

Opciones:

```bash
# Mostrar el contrato JSON completo
bash test-order-flow.command ASTS --url wss://TU_DOMINIO_NGROK/ws/order-flow --json

# Terminar despues del primer evento de datos
bash test-order-flow.command ASTS --url wss://TU_DOMINIO_NGROK/ws/order-flow --once
```

Ctrl+C termina la escucha. Ante un corte, se reconecta y vuelve a suscribirse;
no recupera eventos anteriores. El servidor y ngrok deben seguir funcionando.
El dominio de ngrok puede cambiar; usar siempre el informado por el operador.

## Validacion

El cliente Python tiene pruebas de autenticacion, suscripcion y recepcion en
un WebSocket real de loopback. El lanzador se verifica con Bash en Linux;
no se ejecuto sobre hardware macOS en este entorno de desarrollo.
