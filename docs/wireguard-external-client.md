# Cliente externo MarketBot mediante WireGuard

Este instructivo configura una notebook Windows para acceder al JetStream de MarketBot mediante
una VPN WireGuard. El cliente se conectará a:

```text
Notebook → WireGuard → 10.77.77.1:4222 → NATS JetStream
```

NATS no está publicado directamente en Internet. El único puerto público requerido es UDP
`51820`, utilizado por WireGuard.

## Datos que debe proporcionar el administrador

Antes de completar el túnel, solicitar:

- La clave pública WireGuard del host.
- La IP pública o nombre DDNS del host.
- La IP WireGuard asignada a esta notebook. Para el primer cliente es `10.77.77.2`.

Nunca intercambiar claves privadas.

## 1. Instalar WireGuard

Abrir PowerShell como administrador y ejecutar en una sola línea:

```powershell
winget install --id WireGuard.WireGuard --exact --silent --accept-package-agreements --accept-source-agreements
```

Luego abrir **WireGuard** desde el menú Inicio.

## 2. Generar la clave del cliente y enviarla al administrador

Copiar `configure-wireguard-client.ps1` a la notebook y ejecutarlo en PowerShell. En la primera
etapa no se necesita ningún dato secreto del host:

```powershell
.\configure-wireguard-client.ps1
```

El script crea el par de claves localmente y devuelve JSON similar a:

```json
{
  "status": "client-key-ready",
  "client_public_key": "CLAVE_PUBLICA_DE_LA_NOTEBOOK",
  "client_address_requested": "10.77.77.2/24",
  "private_key_shared": false
}
```

Enviar al administrador solamente `client_public_key`. La clave privada queda protegida en
`%LOCALAPPDATA%\MarketBot\WireGuard` y nunca aparece en la salida del script. Ejecutar nuevamente
esta etapa reutiliza la misma clave, por lo que no invalida una clave pública ya registrada.

## 3. Configuración que realiza el administrador

El administrador registra la clave pública de la notebook en el host y le asigna una dirección
exclusiva:

```ini
[Peer]
PublicKey = CLAVE_PUBLICA_DE_LA_NOTEBOOK
AllowedIPs = 10.77.77.2/32
```

Para clientes posteriores se utilizarán direcciones diferentes, como `10.77.77.3`,
`10.77.77.4`, etc. Cada cliente debe tener su propio par de claves.

El administrador devolverá:

- `CLAVE_PUBLICA_DEL_HOST`
- `IP_PUBLICA_O_DDNS_DEL_HOST:51820`
- La dirección WireGuard asignada al cliente

## 4. Completar el túnel de la notebook

Cuando el administrador devuelva los tres datos, ejecutar el mismo script indicando la clave
pública del host, el endpoint y la IP asignada:

```powershell
.\configure-wireguard-client.ps1 -HostPublicKey "CLAVE_PUBLICA_DEL_HOST" -Endpoint "IP_PUBLICA_O_DDNS:51820" -ClientAddress "10.77.77.2/24"
```

El script reutiliza la clave privada creada en el paso 2 y genera:

```text
%LOCALAPPDATA%\MarketBot\WireGuard\marketbot-client.conf
```

Ese archivo queda con esta estructura:

```ini
[Interface]
PrivateKey = CLAVE_PRIVADA_GENERADA_POR_WIREGUARD
Address = 10.77.77.2/24

[Peer]
PublicKey = CLAVE_PUBLICA_DEL_HOST
AllowedIPs = 10.77.77.1/32
Endpoint = IP_PUBLICA_O_DDNS_DEL_HOST:51820
PersistentKeepalive = 25
```

Abrir WireGuard, elegir **Import tunnel(s) from file**, seleccionar `marketbot-client.conf` y
activarlo manualmente. El script no instala ni inicia un servicio de túnel.

La ruta `AllowedIPs = 10.77.77.1/32` crea un split tunnel: solamente el tráfico dirigido al host
MarketBot utiliza la VPN. La navegación normal de la notebook no pasa por el host y el cliente no
obtiene acceso al resto de la red local.

## 5. Activar y verificar la VPN

El administrador debe activar primero el túnel `marketbot` en el host. Luego presionar
**Activate** en la notebook.

En PowerShell, comprobar que el host responde por la VPN:

```powershell
ping 10.77.77.1
Test-NetConnection 10.77.77.1 -Port 4222
```

El resultado de `Test-NetConnection` debe incluir:

```text
TcpTestSucceeded : True
```

Un ping bloqueado no necesariamente representa un problema; la prueba determinante para el
conector es TCP `4222`.

El acceso debe dejar de funcionar al presionar **Deactivate**. No intentar conectarse directamente
a `IP_PUBLICA:4222`: ese puerto no se publica en Internet.

## 6. Instalar el conector independiente

El administrador entregará un archivo similar a:

```text
marketbot_connector-0.1.0-py3-none-any.whl
```

Este paquete no contiene los engines ni el resto del código de MarketBot. Instalarlo dentro del
entorno Python de la notebook:

```powershell
uv venv --python 3.11
uv pip install .\marketbot_connector-0.1.0-py3-none-any.whl
```

## 7. Probar el conector MarketBot

No es necesario clonar el repositorio completo. Configurar el endpoint VPN:

```powershell
$env:MARKETBOT_CONNECTOR_URL = "nats://10.77.77.1:4222"
$env:MARKETBOT_CONNECTOR_STREAM = "MARKETBOT"
```

Listar engines disponibles:

```powershell
marketbot-connector list-engines
```

Recibir el último mensaje retenido de QQQ y continuar en vivo:

```powershell
marketbot-connector subscribe --subject "marketbot.v1.analysis.result.SWING.QQQ" --batch-size 1 --max-ack-pending 10
```

Recibir todos los mensajes del engine swing:

```powershell
marketbot-connector subscribe --engine swing
```

La salida utiliza JSON Lines: cada línea es un mensaje JSON completo. Detener el proceso con
`Ctrl+C`.

Para visualizar cada mensaje formateado:

```powershell
marketbot-connector subscribe --subject "marketbot.v1.analysis.result.SWING.QQQ" --batch-size 1 --max-ack-pending 10 |
  ForEach-Object {
    $_ | ConvertFrom-Json | ConvertTo-Json -Depth 100
  }
```

Sin `--start-at`, el conector comienza con el último mensaje retenido por subject y luego continúa
en vivo. Para reanudar una posición confirmada entre ejecuciones se puede solicitar al
administrador un identificador durable acordado.

## Diagnóstico rápido

Si el handshake no aparece:

1. Confirmar que ambos túneles estén activados.
2. Revisar que `Endpoint` termine en `:51820`.
3. Confirmar que la notebook tenga conexión a Internet.
4. Verificar con el administrador que la clave pública registrada sea la de esta notebook.
5. Confirmar que la dirección WireGuard no esté asignada a otro cliente.

Si el handshake funciona pero TCP `4222` falla, informar al administrador: debe revisar la
publicación de Docker y la regla de Windows Firewall para la IP WireGuard de esta notebook.
