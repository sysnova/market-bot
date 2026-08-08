# MarketBot Connector

Cliente Python independiente para consumir el stream JetStream existente de MarketBot mediante
una conexión NATS confiable, normalmente a través de WireGuard.

## Instalación

```powershell
uv pip install .\marketbot_connector-0.1.0-py3-none-any.whl
```

## CLI

```powershell
$env:MARKETBOT_CONNECTOR_URL = "nats://10.77.77.1:4222"
marketbot-connector list-engines
marketbot-connector subscribe --engine intraday
```

## API Python

```python
import asyncio

from marketbot_connector import ConnectorConfig, MarketBotSubscriber, resolve_filters


async def main() -> None:
    subscriber = await MarketBotSubscriber.connect(
        ConnectorConfig(
            url="nats://10.77.77.1:4222",
            filters=resolve_filters(engines=("intraday",)),
            durable_name="external-analysis-service",
        )
    )
    try:
        await subscriber.run(lambda message: process(message.to_jsonable()))
    finally:
        await subscriber.close()


async def process(message: dict[str, object]) -> None:
    print(message)


asyncio.run(main())
```

El handler confirma el mensaje al terminar correctamente. Si lanza una excepción, el mensaje se
marca para reentrega. Cada servicio que necesite recibir una copia completa debe usar un durable
propio.
