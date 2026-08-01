"""Market-regime overlay calculated from tradable Alpaca proxies."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.contracts import MacroRegime, MarketBar, NamedValue

from .indicators import atr, sma

REQUIRED_SYMBOLS = ("UUP", "VIXY", "TLT", "IEF", "SPY")


@dataclass(frozen=True, slots=True)
class MacroAssessment:
    regime: MacroRegime
    adverse_signals: tuple[str, ...]
    shock_signals: tuple[str, ...]
    metrics: tuple[NamedValue, ...] = ()


def classify_macro_regime(series: dict[str, tuple[MarketBar, ...]]) -> MacroAssessment:
    if any(len(series.get(symbol, ())) < 200 for symbol in REQUIRED_SYMBOLS):
        return MacroAssessment(MacroRegime.UNKNOWN, ("macro_history_missing",), ())
    adverse: list[str] = []
    shocks: list[str] = []
    values = {symbol: series[symbol] for symbol in REQUIRED_SYMBOLS}
    freshness_cutoff = values["SPY"][-3].timestamp
    if any(values[symbol][-1].timestamp < freshness_cutoff for symbol in REQUIRED_SYMBOLS):
        return MacroAssessment(MacroRegime.UNKNOWN, ("macro_proxy_stale",), ())
    metrics: list[NamedValue] = []
    for symbol, bars in values.items():
        closes = tuple(bar.close for bar in bars)
        current_atr = atr(bars, 14)
        metrics.extend((
            NamedValue(name=f"{symbol.lower()}_close", value=bars[-1].close),
            NamedValue(name=f"{symbol.lower()}_sma50", value=sma(closes, 50)),
            NamedValue(name=f"{symbol.lower()}_sma200", value=sma(closes, 200)),
            NamedValue(
                name=f"{symbol.lower()}_sma50_slope_5",
                value=sma(closes, 50) - sma(closes[:-5], 50),
            ),
            NamedValue(
                name=f"{symbol.lower()}_move_5_atr",
                value=(bars[-1].close - bars[-6].close) / current_atr,
            ),
            NamedValue(name=f"{symbol.lower()}_atr14", value=current_atr),
        ))

    spy = values["SPY"]
    spy_closes = tuple(bar.close for bar in spy)
    spy_sma50 = sma(spy_closes, 50)
    spy_sma50_prior = sma(spy_closes[:-5], 50)
    if spy[-1].close < spy_sma50 and spy_sma50 < spy_sma50_prior:
        adverse.append("spy_below_falling_sma50")

    vixy = values["VIXY"]
    vixy_atrs = tuple(atr(vixy[:index], 14) for index in range(15, len(vixy) + 1))
    current_vixy_atr = vixy_atrs[-1]
    vixy_atr_percentile = Decimal(
        sum(item < current_vixy_atr for item in vixy_atrs)
    ) / Decimal(len(vixy_atrs))
    metrics.append(NamedValue(name="vixy_atr_percentile", value=vixy_atr_percentile))
    if (
        vixy[-1].close > sma(tuple(bar.close for bar in vixy), 50)
        or vixy_atr_percentile > Decimal("0.75")
    ):
        adverse.append("vixy_above_sma50")

    uup = values["UUP"]
    uup_closes = tuple(bar.close for bar in uup)
    if uup[-1].close > sma(uup_closes, 50) and sma(uup_closes, 50) > sma(uup_closes[:-5], 50):
        adverse.append("uup_strong_dollar")

    if all(
        values[symbol][-1].close < sma(tuple(bar.close for bar in values[symbol]), 50)
        for symbol in ("TLT", "IEF")
    ):
        adverse.append("treasury_etfs_below_sma50")

    for symbol in ("UUP", "VIXY", "TLT"):
        bars = values[symbol]
        move_atr = abs(bars[-1].close - bars[-6].close) / atr(bars, 14)
        if move_atr >= Decimal("2"):
            shocks.append(f"{symbol.lower()}_five_day_shock")
    if (spy[-6].close - spy[-1].close) / atr(spy, 14) >= Decimal("2"):
        shocks.append("spy_five_day_selloff")

    if shocks:
        regime = MacroRegime.SHOCK
    elif len(adverse) >= 2:
        regime = MacroRegime.RISK_OFF
    elif len(adverse) == 1:
        regime = MacroRegime.NEUTRAL
    else:
        regime = MacroRegime.RISK_ON
    return MacroAssessment(regime, tuple(adverse), tuple(shocks), tuple(metrics))
