# Alpaca DataFrame Connector

Conector Python de sólo lectura para reemplazar las descargas históricas de `yfinance`
por solicitudes REST a Alpaca. Recibe una lista de tickers y devuelve un
`dict[str, pandas.DataFrame]`, con un DataFrame OHLCV por ticker.

No abre WebSockets, no utiliza la Trading API y no puede enviar órdenes.

## Requisitos

- Python 3.10 o posterior.
- Una credencial de Alpaca compuesta por **API Key ID** y **Secret Key**.
- Acceso al feed configurado en la cuenta de Alpaca.

## Instalación en el proyecto consumidor

Desde la raíz de ese proyecto:

```powershell
python -m pip install "C:\Users\lgonz\Projects\market-bot\packages\alpaca-dataframe-connector"
```

También se puede agregar como dependencia local en `requirements.txt` o `pyproject.toml`.

## Configuración

Copiar `alpaca.example.toml` como `alpaca.toml` y elegir una de estas alternativas:

1. Recomendada: conservar los placeholders y definir las variables de entorno.
2. Escribir los dos valores directamente en el TOML local. `alpaca.toml` está excluido
   por el `.gitignore` del paquete para evitar publicar las credenciales.

Ejemplo para PowerShell:

```powershell
$env:APCA_API_KEY_ID = "su-api-key-id"
$env:APCA_API_SECRET_KEY = "su-secret-key"
```

Alpaca requiere ambos valores; una API key sin su secret no puede autenticar solicitudes.

### Elección del feed

- `sip`: datos consolidados de los mercados estadounidenses. Es la mejor opción para
  aproximar Yahoo Finance y la recomendada para este scanner porque usa volumen y CMF.
  Requiere que la suscripción de Alpaca tenga acceso a SIP.
- `iex`: funciona con el acceso básico, pero representa sólo el mercado IEX. Precio y,
  especialmente, volumen pueden diferir de Yahoo/SIP y alterar señales técnicas.

`adjustment = "all"` solicita barras ajustadas por splits y dividendos. Esto aproxima el
comportamiento histórico ajustado de Yahoo, aunque los proveedores pueden conservar
diferencias menores de metodología.

## Integración con el scanner existente

Antes, típicamente:

```python
df = yf.download(ticker, period="3y", interval="1d")
resultado = scanner_maestro_por_df(df, ticker=ticker)
```

Con Alpaca:

```python
from alpaca_dataframe_connector import AlpacaDataClient

tickers = ["AAPL", "MSFT", "NVDA"]

with AlpacaDataClient.from_config("alpaca.toml") as client:
    dataframes = client.download(tickers, period="3y", interval="1d")

resultados = []
for ticker, df in dataframes.items():
    if df.empty:
        print(f"[{ticker}] Alpaca no devolvió barras")
        continue
    resultado = scanner_maestro_por_df(df, ticker=ticker)
    if resultado is not None:
        resultados.append(resultado)
```

Con `interval="1d"`, la salida de cada ticker tiene:

- índice `DatetimeIndex` llamado `Date`, ascendente y sin zona horaria;
- columnas exactas `Open`, `High`, `Low`, `Close`, `Volume`;
- una fila por rueda diaria;
- columnas simples, nunca `MultiIndex`.

Ese contrato satisface todas las lecturas del código provisto: precios OHLC, volumen,
Bollinger Bands, MACD, RSI, medias móviles y CMF. El método conserva DataFrames vacíos
para tickers sin datos, para que el consumidor pueda decidir si omitirlos o informar el
problema.

## Períodos e intervalos parametrizables

`period` controla cuánto histórico se solicita y `interval` controla el tamaño de cada
vela. Son parámetros independientes:

```python
with AlpacaDataClient.from_config("alpaca.toml") as client:
    velas_1_minuto = client.download(
        ["AAPL", "MSFT"], period="5d", interval="1m"
    )
    velas_4_horas = client.download(
        ["AAPL", "MSFT"], period="6mo", interval="4h"
    )
    velas_diarias = client.download(
        ["AAPL", "MSFT"], period="3y", interval="1d"
    )
```

Períodos aceptados:

- minutos: `30m`, `90m`;
- horas: `12h`, `48h`;
- días: `1d`, `5d`, `30d`;
- semanas: `1wk`, `2wk`;
- meses calendario: `1mo`, `3mo`, `6mo`;
- años: `1y`, `3y`, `10y`;
- especiales: `ytd` y `max`.

Intervalos aceptados por Alpaca:

- minutos de `1m` a `59m`;
- horas de `1h` a `23h`, incluido `4h`;
- `1d` y `1wk`;
- `1mo`, `2mo`, `3mo`, `6mo` y `12mo`.

También se aceptan las formas explícitas `1Min`, `4Hour`, `1Day`, `1Week` y
`1Month`. Los intervalos intradiarios devuelven un índice `Datetime` con zona horaria
`America/New_York`; los diarios o superiores mantienen un índice `Date` sin zona
horaria.

El scanner entregado fue diseñado originalmente para velas diarias. Puede recibir un
DataFrame intradiario, pero entonces una SMA 200 representa 200 velas del intervalo y no
200 ruedas diarias. Para conservar la semántica original se debe seguir usando `1d`.

## Rango explícito

También se pueden indicar fechas con zona horaria:

```python
from datetime import datetime, timezone

dataframes = client.get_daily_bars(
    ["AAPL", "MSFT"],
    start=datetime(2023, 1, 1, tzinfo=timezone.utc),
    end=datetime(2026, 1, 1, tzinfo=timezone.utc),
)
```

Para un intervalo explícito diferente de un día:

```python
dataframes = client.get_bars(
    ["AAPL", "MSFT"],
    interval="4h",
    start=datetime(2026, 1, 1, tzinfo=timezone.utc),
    end=datetime(2026, 8, 1, tzinfo=timezone.utc),
)
```

## Comportamiento operativo

- Usa `GET https://data.alpaca.markets/v2/stocks/bars`.
- Autentica con `APCA-API-KEY-ID` y `APCA-API-SECRET-KEY`.
- Agrupa tickers, pagina resultados y limita cada página a 10.000 barras.
- Reintenta errores transitorios y HTTP 429 respetando `Retry-After`.
- Nunca incluye credenciales en mensajes de error.
- Permite períodos e intervalos independientes dentro de los límites de Alpaca.

Referencias oficiales: [Alpaca Historical Stock Bars](https://docs.alpaca.markets/reference/stockbars)
y [Alpaca TimeFrame](https://alpaca.markets/sdks/python/api_reference/data/timeframe.html).

## Pruebas

```powershell
uv run pytest
uv run ruff check .
```
