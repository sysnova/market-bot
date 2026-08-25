"""Scanner tecnico para estructuras Long y Swing en contexto diario.

Mantiene los nombres publicos del scanner original, pero reorganiza la logica
para que:

- Long capture una estructura sana, no solo un rebote aislado.
- Swing detecte mejor la entrada dentro de una estructura Long valida.
- El carril especulativo quede separado y sea mas conservador.

El contrato de salida se conserva: los carriles Long/Swing devuelven dict con
`Aprobado` o `Activo`, y el scanner maestro devuelve `None` cuando no hay
senal para mantener compatibilidad con el ejemplo original.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import ta

REQUERIDAS = ("Open", "High", "Low", "Close", "Volume")
MIN_BARRAS = 220


def _normalizar_dataframe(df: pd.DataFrame) -> pd.DataFrame | None:
    if df is None or df.empty:
        return None

    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = df.columns.get_level_values(0)

    faltantes = [col for col in REQUERIDAS if col not in df.columns]
    if faltantes:
        return None

    df = df.copy()
    df = df.sort_index()
    df = df.dropna(subset=list(REQUERIDAS))
    if len(df) < MIN_BARRAS:
        return None

    for col in REQUERIDAS:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=list(REQUERIDAS))
    if len(df) < MIN_BARRAS:
        return None

    return df


def _slope_pct(series: pd.Series, window: int) -> pd.Series:
    base = series.shift(window)
    return (series / base - 1.0) * 100.0


def _pct_distancia(precio: pd.Series, referencia: pd.Series) -> pd.Series:
    return ((precio - referencia) / precio) * 100.0


def _pct_espacio_libre(precio: pd.Series, resistencia: pd.Series) -> pd.Series:
    return ((resistencia - precio) / precio) * 100.0


def _vela_fuerte(df: pd.DataFrame) -> pd.Series:
    rango = (df["High"] - df["Low"]).replace(0, pd.NA)
    return ((df["Close"] - df["Low"]) / rango) >= 0.60


def _volumen_confirmado(df: pd.DataFrame, factor: float = 1.15) -> pd.Series:
    return df["Volume"] >= (df["vol_sma_20"] * factor)


def _long_contexto_valido(ultimo: pd.Series) -> bool:
    condiciones = [
        pd.notna(ultimo.get("sma_200")),
        pd.notna(ultimo.get("sma_50")),
        pd.notna(ultimo.get("sma_20")),
        pd.notna(ultimo.get("rsi")),
        pd.notna(ultimo.get("cmf")),
        pd.notna(ultimo.get("adx_14")),
    ]
    return all(condiciones)


def preparar_datos_tecnicos(df: pd.DataFrame) -> pd.DataFrame | None:
    """
    Calcula indicadores de contexto, estructura y gatillos.

    La salida esta pensada para detectar:
    - estructura Long sana;
    - entrada Swing diaria dentro de esa estructura;
    - carril especulativo separado por debajo de SMA 200.
    """
    df = _normalizar_dataframe(df)
    if df is None:
        return None

    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    volume = df["Volume"]

    bb = ta.volatility.BollingerBands(close=close, window=20, window_dev=2.0)
    df["bb_low"] = bb.bollinger_lband()
    df["bb_high"] = bb.bollinger_hband()
    df["bb_mid"] = bb.bollinger_mavg()
    df["bb_width"] = (df["bb_high"] - df["bb_low"]) / df["bb_mid"]
    df["bb_width_min_120"] = df["bb_width"].rolling(window=120, min_periods=60).min()
    df["compresion_volatilidad"] = df["bb_width"] <= (df["bb_width_min_120"] * 1.08)

    macd = ta.trend.MACD(close=close)
    df["macd"] = macd.macd()
    df["macd_signal"] = macd.macd_signal()
    df["macd_hist"] = df["macd"] - df["macd_signal"]
    df["macd_hist_prev"] = df["macd_hist"].shift(1)
    df["macd_hist_slope_3"] = _slope_pct(df["macd_hist"], 3)

    df["rsi"] = ta.momentum.rsi(close=close, window=14)
    df["ema_20"] = ta.trend.ema_indicator(close=close, window=20)
    df["ema_50"] = ta.trend.ema_indicator(close=close, window=50)
    df["sma_20"] = ta.trend.sma_indicator(close=close, window=20)
    df["sma_50"] = ta.trend.sma_indicator(close=close, window=50)
    df["sma_150"] = ta.trend.sma_indicator(close=close, window=150)
    df["sma_200"] = ta.trend.sma_indicator(close=close, window=200)
    df["sma_20_slope_5_pct"] = _slope_pct(df["sma_20"], 5)
    df["sma_50_slope_10_pct"] = _slope_pct(df["sma_50"], 10)
    df["sma_200_slope_20_pct"] = _slope_pct(df["sma_200"], 20)

    adx = ta.trend.ADXIndicator(high=high, low=low, close=close, window=14)
    df["adx_14"] = adx.adx()
    df["di_plus_14"] = adx.adx_pos()
    df["di_minus_14"] = adx.adx_neg()

    df["vol_sma_20"] = volume.rolling(window=20, min_periods=10).mean()
    df["vol_sma_50"] = volume.rolling(window=50, min_periods=20).mean()
    df["rvol_20"] = df["Volume"] / df["vol_sma_20"]

    df["cmf"] = ta.volume.chaikin_money_flow(
        high=high,
        low=low,
        close=close,
        volume=volume,
        window=20,
    )

    df["atr_14"] = ta.volatility.average_true_range(
        high=high,
        low=low,
        close=close,
        window=14,
    )

    df["resistencia_inmediata_prev"] = high.shift(1).rolling(window=20, min_periods=10).max()
    df["soporte_inmediato_prev"] = low.shift(1).rolling(window=10, min_periods=5).min()
    df["resistencia_estructural_prev"] = high.shift(1).rolling(window=252, min_periods=120).max()
    df["soporte_estructural_prev"] = low.shift(1).rolling(window=252, min_periods=120).min()

    df["espacio_resistencia_inmediata_pct"] = _pct_espacio_libre(
        df["Close"],
        df["resistencia_inmediata_prev"],
    )
    df["espacio_libre_pct"] = _pct_espacio_libre(
        df["Close"],
        df["resistencia_estructural_prev"],
    )
    df["distancia_soporte_inmediato_pct"] = _pct_distancia(
        df["Close"],
        df["soporte_inmediato_prev"],
    )

    df["cierre_fuerte"] = _vela_fuerte(df)
    df["volumen_fuerte_115"] = _volumen_confirmado(df, factor=1.15)
    df["volumen_fuerte_125"] = _volumen_confirmado(df, factor=1.25)
    df["reclaim_sma20"] = (df["Close"] > df["sma_20"]) & (df["Low"] <= df["sma_20"] * 1.01)
    df["reclaim_sma50"] = (df["Close"] > df["sma_50"]) & (df["Low"] <= df["sma_50"] * 1.01)
    df["close_sobre_resistencia_inmediata"] = df["Close"] > (
        df["resistencia_inmediata_prev"] * 1.003
    )

    df["condicion_descuento"] = (
        (df["Close"] <= df["bb_low"] * 1.02)
        | (df["Close"] < df["sma_20"] * 0.985)
        | (df["Close"] < df["sma_50"] * 0.97)
    )

    df["valle_reciente"] = (
        df["condicion_descuento"].rolling(window=15, min_periods=1).max().astype(bool)
    )
    df["freno_zona_valle"] = df["valle_reciente"] & (
        df["Close"] >= (df["soporte_inmediato_prev"] * 1.00)
    )

    df["condicion_disparo_tactico"] = (
        (df["macd_hist"] > df["macd_hist_prev"])
        | (df["rvol_20"] >= 1.15)
        | (df["cierre_fuerte"] & (df["Volume"] >= df["vol_sma_20"]))
    )
    df["valle_y_disparo_reciente"] = (
        df["condicion_descuento"] & df["condicion_disparo_tactico"]
    ).rolling(window=15, min_periods=1).max().astype(bool)
    df["ciclo_tactico_vigente"] = df["valle_y_disparo_reciente"] & (
        df["Close"] >= (df["soporte_inmediato_prev"] * 1.00)
    )

    rango_actual = df["High"] - df["Low"]
    rango_prom = rango_actual.rolling(window=14, min_periods=5).mean()
    vela_expansiva = rango_actual >= (rango_prom * 1.10)
    df["estallido_especulativo_puntual"] = (
        (df["Close"] <= df["bb_low"] * 1.02)
        & df["volumen_fuerte_125"]
        & vela_expansiva
        & df["cierre_fuerte"]
    )
    df["estallido_especulativo_reciente"] = (
        df["estallido_especulativo_puntual"].rolling(window=15, min_periods=1).max().astype(bool)
    )
    df["ciclo_especulativo_vigente"] = df["estallido_especulativo_reciente"] & (
        df["Close"] >= (df["soporte_inmediato_prev"] * 1.00)
    )

    return df


def evaluar_estructura_long_diaria(
    df: pd.DataFrame, umbral_espacio_min: float = 8.0
) -> dict[str, Any]:
    """
    Evalua si la estructura Long diaria esta sana.

    La idea es distinguir estructura de entrada:
    - estructura: tendencia, medias, momentum y espacio;
    - entrada: se evalua aparte en Swing.
    """
    ultimo = df.iloc[-1]
    if not _long_contexto_valido(ultimo):
        return {"Aprobado": False}

    close = float(ultimo["Close"])
    sma_20 = float(ultimo["sma_20"])
    sma_50 = float(ultimo["sma_50"])
    sma_200 = float(ultimo["sma_200"])
    rsi = float(ultimo["rsi"])
    cmf = float(ultimo["cmf"])
    adx = float(ultimo["adx_14"])
    di_plus = float(ultimo["di_plus_14"])
    di_minus = float(ultimo["di_minus_14"])

    tendencia_base = (
        close > sma_200
        and sma_20 > sma_50
        and sma_50 > sma_200
        and close > float(ultimo["ema_20"])
        and close > float(ultimo["ema_50"])
    )

    pendiente_ok = (
        float(ultimo["sma_50_slope_10_pct"]) > 0
        and float(ultimo["sma_200_slope_20_pct"]) >= -0.20
    )

    momentum_ok = (
        rsi >= 55.0
        and cmf > 0.02
        and adx >= 18.0
        and di_plus > di_minus
        and float(ultimo["macd_hist"]) >= float(ultimo["macd_hist_prev"])
    )

    espacio_estructural = float(ultimo["espacio_libre_pct"])
    tiene_espacio = pd.notna(espacio_estructural) and espacio_estructural >= umbral_espacio_min

    if tendencia_base and pendiente_ok and momentum_ok and tiene_espacio:
        return {
            "Aprobado": True,
            "Tipo": "Estructura Long sana / impulso en continuidad",
            "Reco": (
                "COMPRA (Estructura Long sana, "
                f"{espacio_estructural:.1f}% de recorrido estructural libre)"
            ),
            "Puntaje": 4.0,
        }

    return {"Aprobado": False}


def evaluar_entrada_swing_diaria(df: pd.DataFrame, umbral_rr: float = 1.5) -> dict[str, Any]:
    """
    Busca una entrada Swing dentro de una estructura Long valida.

    Dos modos principales:
    - pullback: retroceso ordenado a SMA 20 / SMA 50 con recuperacion;
    - breakout: ruptura de resistencia inmediata con volumen y cierre fuerte.
    """
    ultimo = df.iloc[-1]
    if not _long_contexto_valido(ultimo):
        return {"Aprobado": False}

    close = float(ultimo["Close"])
    open_ = float(ultimo["Open"])
    low = float(ultimo["Low"])
    sma_20 = float(ultimo["sma_20"])
    sma_50 = float(ultimo["sma_50"])
    sma_200 = float(ultimo["sma_200"])
    rsi = float(ultimo["rsi"])
    cmf = float(ultimo["cmf"])
    atr = float(ultimo["atr_14"])
    vol_rvol = float(ultimo["rvol_20"]) if pd.notna(ultimo["rvol_20"]) else 0.0
    resistencia = float(ultimo["resistencia_inmediata_prev"])
    soporte = float(ultimo["soporte_inmediato_prev"])

    estructura_ok = (
        close > sma_200
        and sma_50 > sma_200
        and sma_20 >= sma_50 * 0.97
    )

    espacio_inmediato = ultimo["espacio_resistencia_inmediata_pct"]
    espacio_ok = pd.notna(espacio_inmediato) and float(espacio_inmediato) >= 3.0

    pullback_ordenado = (
        close >= sma_20 * 0.985
        and close <= sma_50 * 1.015
        and low >= soporte * 0.985
    )
    pullback_confirmado = (
        pullback_ordenado
        and close > open_
        and rsi >= 48.0
        and cmf > 0.0
        and (
            float(ultimo["macd_hist"]) >= float(ultimo["macd_hist_prev"])
            or float(ultimo["macd_hist_slope_3"]) > 0
        )
        and (vol_rvol >= 1.0 or bool(ultimo["reclaim_sma20"]) or bool(ultimo["reclaim_sma50"]))
    )

    breakout_confirmado = (
        bool(ultimo["close_sobre_resistencia_inmediata"])
        and bool(ultimo["cierre_fuerte"])
        and (vol_rvol >= 1.20 or bool(ultimo["volumen_fuerte_115"]))
        and rsi >= 52.0
        and cmf > 0.0
        and close > open_
    )

    if not estructura_ok:
        return {"Aprobado": False}

    if pullback_confirmado and espacio_ok:
        riesgo_pct = ((close - (soporte - atr * 0.25)) / close) * 100.0 if atr > 0 else None
        return {
            "Aprobado": True,
            "Tipo": "Swing de Pullback dentro de Long sano",
            "Modo": "PULLBACK",
            "Reco": (
                "COMPRA (Swing de pullback dentro de estructura Long, "
                f"{float(espacio_inmediato):.1f}% hasta resistencia inmediata)"
            ),
            "Puntaje": 3.9,
            "Soporte": round(soporte, 2),
            "Resistencia": round(resistencia, 2),
            "ATR_14": round(atr, 2) if pd.notna(atr) else None,
            "RiesgoPct": round(riesgo_pct, 2) if riesgo_pct is not None else None,
            "RR_Minimo": umbral_rr,
        }

    if breakout_confirmado and espacio_ok:
        riesgo_pct = ((close - (resistencia - atr * 0.50)) / close) * 100.0 if atr > 0 else None
        return {
            "Aprobado": True,
            "Tipo": "Swing de ruptura dentro de Long sano",
            "Modo": "BREAKOUT",
            "Reco": (
                "COMPRA (Swing de ruptura con "
                f"{float(espacio_inmediato):.1f}% de aire inmediato)"
            ),
            "Puntaje": 4.1,
            "Soporte": round(soporte, 2),
            "Resistencia": round(resistencia, 2),
            "ATR_14": round(atr, 2) if pd.notna(atr) else None,
            "RiesgoPct": round(riesgo_pct, 2) if riesgo_pct is not None else None,
            "RR_Minimo": umbral_rr,
        }

    return {"Aprobado": False}


def analizar_carril_impulso_y_espacio(
    df: pd.DataFrame, umbral_espacio_min: float = 8.0
) -> dict[str, Any]:
    ultimo = df.iloc[-1]
    vol_sma = ultimo.get("vol_sma_20")
    if pd.isna(vol_sma) or vol_sma <= 0:
        return {"Aprobado": False}

    res_estructura = evaluar_estructura_long_diaria(df, umbral_espacio_min=umbral_espacio_min)
    if res_estructura.get("Aprobado"):
        return res_estructura

    return {"Aprobado": False}


def analizar_carril_tactico_descuento(
    df: pd.DataFrame, umbral_espacio_min: float = 3.0
) -> dict[str, Any]:
    ultimo = df.iloc[-1]
    if pd.isna(ultimo.get("vol_sma_20")) or ultimo.get("vol_sma_20", 0) <= 0:
        return {"Aprobado": False}

    if pd.notna(ultimo.get("sma_200")) and float(ultimo["Close"]) < float(ultimo["sma_200"]):
        return {"Aprobado": False}

    res_swing = evaluar_entrada_swing_diaria(df, umbral_rr=1.5)
    if res_swing.get("Aprobado"):
        espacio = float(ultimo["espacio_resistencia_inmediata_pct"])
        if pd.notna(espacio) and espacio >= umbral_espacio_min:
            return res_swing

    return {"Aprobado": False}


def analizar_carril_especulativo(
    df: pd.DataFrame, umbral_espacio_min: float = 8.0
) -> dict[str, Any]:
    """
    Carril bajo SMA 200.

    Se mantiene por compatibilidad, pero se usa como alerta o rescue, no como
    sustituto del Long/Swing estructural.
    """
    if len(df) < 190:
        return {"Activo": False}

    ultimo = df.iloc[-1]
    close = float(ultimo["Close"])
    sma_200 = ultimo.get("sma_200")
    vol_sma = ultimo.get("vol_sma_20")

    if pd.isna(vol_sma) or vol_sma <= 0:
        return {"Activo": False}

    if pd.notna(sma_200) and close >= float(sma_200):
        return {"Activo": False}

    espacio = ultimo.get("espacio_libre_pct")
    if pd.isna(espacio) or float(espacio) < umbral_espacio_min:
        return {"Activo": False}

    cmf_positivo = pd.notna(ultimo.get("cmf")) and float(ultimo["cmf"]) > 0.05
    min_20 = df["Low"].tail(20).min()
    recuperacion_basica = close >= (min_20 * 1.015) if pd.notna(min_20) else False

    if (
        bool(ultimo["compresion_volatilidad"])
        and bool(ultimo["close_sobre_resistencia_inmediata"])
        and cmf_positivo
        and float(ultimo["Volume"]) >= float(vol_sma) * 1.25
    ):
        return {
            "Activo": True,
            "Tipo": "Compresion de volatilidad / ruptura bajo SMA 200",
            "Modo": "ESPECULATIVO / RUPTURA",
            "Recomendacion": f"COMPRA (Ruptura con {float(espacio):.1f}% libre)",
            "Puntaje": 3.2,
        }

    if bool(ultimo["ciclo_especulativo_vigente"]) and recuperacion_basica and cmf_positivo:
        return {
            "Activo": True,
            "Tipo": "Rescate tecnico bajo SMA 200",
            "Modo": "ESPECULATIVO / CICLICO",
            "Recomendacion": f"ALERTA (Rescate tecnico con {float(espacio):.1f}% libre)",
            "Puntaje": 2.6,
        }

    if bool(ultimo["freno_zona_valle"]) and pd.notna(sma_200) and close >= (float(sma_200) * 0.85):
        return {
            "Activo": True,
            "Tipo": "Alerta de seguimiento de valle bajo SMA 200",
            "Modo": "SEGUIMIENTO / OBSERVACION",
            "Recomendacion": f"ALERTA (Freno en valle con {float(espacio):.1f}% libre)",
            "Puntaje": 1.8,
        }

    return {"Activo": False}


def scanner_maestro_por_df(df: pd.DataFrame, ticker: str = "ACTIVO") -> dict[str, Any] | None:
    """
    Scanner maestro con jerarquia:
    1. Estructura Long sana.
    2. Entrada Swing dentro de esa estructura.
    3. Carril especulativo bajo SMA 200.
    """
    df_procesado = preparar_datos_tecnicos(df)
    if df_procesado is None:
        print(f"[{ticker}] ERROR: datos insuficientes (< {MIN_BARRAS} barras).")
        return None

    ultimo = df_procesado.iloc[-1]

    res_impulso = analizar_carril_impulso_y_espacio(df_procesado)
    if res_impulso.get("Aprobado"):
        resultado = {
            "Ticker": ticker,
            "Recomendacion": res_impulso["Reco"],
            "Puntaje_Maestro": res_impulso["Puntaje"],
            "Precio_Actual": round(float(ultimo["Close"]), 2),
            "RSI": round(float(ultimo["rsi"]), 1) if pd.notna(ultimo["rsi"]) else None,
            "CMF": round(float(ultimo["cmf"]), 3) if pd.notna(ultimo["cmf"]) else None,
            "Estrategias_Activas": [res_impulso["Tipo"]],
            "Modo": "TENDENCIA / IMPULSO",
        }
        _imprimir_diagnostico_en_consola(ticker, ultimo, resultado)
        return resultado

    res_descuento = analizar_carril_tactico_descuento(df_procesado)
    if res_descuento.get("Aprobado"):
        resultado = {
            "Ticker": ticker,
            "Recomendacion": res_descuento["Reco"],
            "Puntaje_Maestro": res_descuento["Puntaje"],
            "Precio_Actual": round(float(ultimo["Close"]), 2),
            "RSI": round(float(ultimo["rsi"]), 1) if pd.notna(ultimo["rsi"]) else None,
            "CMF": round(float(ultimo["cmf"]), 3) if pd.notna(ultimo["cmf"]) else None,
            "Estrategias_Activas": [res_descuento["Tipo"]],
            "Modo": "SWING / ENTRADA",
        }
        if "Modo" in res_descuento:
            resultado["Submodo"] = res_descuento["Modo"]
        _imprimir_diagnostico_en_consola(ticker, ultimo, resultado)
        return resultado

    res_esp = analizar_carril_especulativo(df_procesado)
    if res_esp.get("Activo"):
        resultado = {
            "Ticker": ticker,
            "Tipo": res_esp["Tipo"],
            "Modo": res_esp["Modo"],
            "Recomendacion": res_esp["Recomendacion"],
            "Puntaje_Maestro": res_esp["Puntaje"],
            "Precio_Actual": round(float(ultimo["Close"]), 2),
            "RSI": round(float(ultimo["rsi"]), 1) if pd.notna(ultimo["rsi"]) else None,
            "CMF": round(float(ultimo["cmf"]), 3) if pd.notna(ultimo["cmf"]) else None,
            "Estrategias_Activas": [res_esp["Tipo"]],
        }
        _imprimir_diagnostico_en_consola(ticker, ultimo, resultado)
        return resultado

    resultado_base = {
        "Ticker": ticker,
        "Recomendacion": "SIN SENAL",
        "Puntaje_Maestro": 0,
        "Precio_Actual": round(float(ultimo["Close"]), 2),
        "RSI": round(float(ultimo["rsi"]), 1) if pd.notna(ultimo["rsi"]) else None,
        "CMF": round(float(ultimo["cmf"]), 3) if pd.notna(ultimo["cmf"]) else None,
        "Estrategias_Activas": ["Ninguna"],
        "Modo": "ESPERA",
    }
    _imprimir_diagnostico_en_consola(ticker, ultimo, resultado_base)
    return None


def _imprimir_diagnostico_en_consola(
    ticker: str, ultimo: pd.Series, resultado: dict[str, Any]
) -> None:
    """Salida de auditoria legible para consola."""
    print("=" * 72)
    print(f"AUDITORIA TECNICA: {ticker} | Modo: {resultado.get('Modo', 'N/A')}")
    print("-" * 72)
    print(f"Precio Actual             : {round(float(ultimo['Close']), 2)}")
    print(f"Espacio Estructural Anual : {round(float(ultimo['espacio_libre_pct']), 1)}%")
    resistencia_20d = round(float(ultimo["espacio_resistencia_inmediata_pct"]), 1)
    print(f"Espacio Resistencia 20D   : {resistencia_20d}%")
    print(f"Por encima de SMA 20      : {bool(ultimo['Close'] > ultimo['sma_20'])}")
    print(f"Por encima de SMA 50      : {bool(ultimo['Close'] > ultimo['sma_50'])}")
    print(f"Por encima de SMA 200     : {bool(ultimo['Close'] > ultimo['sma_200'])}")
    print(f"RSI                       : {round(float(ultimo['rsi']), 1)}")
    print(f"CMF                       : {round(float(ultimo['cmf']), 3)}")
    print(f"ADX 14                    : {round(float(ultimo['adx_14']), 1)}")
    print(f"Valle Reciente (15R)      : {bool(ultimo['valle_reciente'])}")
    print(f"Freno en Valle            : {bool(ultimo['freno_zona_valle'])}")
    print(f"Compresion Volatilidad    : {bool(ultimo['compresion_volatilidad'])}")
    print(f"Reclaim SMA 20            : {bool(ultimo['reclaim_sma20'])}")
    print(f"Reclaim SMA 50            : {bool(ultimo['reclaim_sma50'])}")
    print("-" * 72)
    print(f"RECOMENDACION             : {resultado.get('Recomendacion', 'N/A')}")
    print("=" * 72 + "\n")
