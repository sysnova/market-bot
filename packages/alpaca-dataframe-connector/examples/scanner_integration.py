"""Ejemplo mínimo para reemplazar yf.download en el scanner provisto."""

# Reemplazar este import por el módulo real que contiene scanner_maestro_por_df.
from mi_scanner import scanner_maestro_por_df

from alpaca_dataframe_connector import AlpacaDataClient


def analizar_tickers(
    tickers: list[str],
    config_path: str = "alpaca.toml",
    period: str = "3y",
    interval: str = "1d",
) -> list[dict]:
    """Descargar el período/intervalo elegido y ejecutar el scanner por ticker."""

    with AlpacaDataClient.from_config(config_path) as client:
        dataframes = client.download(tickers, period=period, interval=interval)

    resultados: list[dict] = []
    for ticker, dataframe in dataframes.items():
        if dataframe.empty:
            print(f"[{ticker}] Alpaca no devolvió barras históricas")
            continue
        resultado = scanner_maestro_por_df(dataframe, ticker=ticker)
        if resultado is not None:
            resultados.append(resultado)
    return resultados


if __name__ == "__main__":
    analizar_tickers(["AAPL", "MSFT", "NVDA"])
