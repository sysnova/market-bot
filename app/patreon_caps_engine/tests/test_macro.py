from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.contracts import BarTimeframe, MacroRegime, MarketBar
from app.patreon_caps_engine.macro import classify_macro_regime

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _series(symbol: str, start: str, step: str) -> tuple[MarketBar, ...]:
    value = Decimal(start)
    delta = Decimal(step)
    bars: list[MarketBar] = []
    for index in range(210):
        close = value + delta * index
        bars.append(
            MarketBar(
                symbol=symbol,
                timeframe=BarTimeframe.DAY_1,
                timestamp=NOW + timedelta(days=index),
                open=close,
                high=close + Decimal("0.5"),
                low=close - Decimal("0.5"),
                close=close,
                volume=Decimal("1000"),
                source="fixture",
                feed="test",
            )
        )
    return tuple(bars)


def test_macro_classifies_constructive_and_missing_inputs() -> None:
    constructive = {
        "SPY": _series("SPY", "400", "1"),
        "VIXY": _series("VIXY", "80", "-0.1"),
        "UUP": _series("UUP", "30", "-0.01"),
        "TLT": _series("TLT", "90", "0.05"),
        "IEF": _series("IEF", "95", "0.03"),
    }

    assert classify_macro_regime(constructive).regime is MacroRegime.RISK_ON
    assert classify_macro_regime({}).regime is MacroRegime.UNKNOWN


def test_macro_classifies_risk_off_and_shock() -> None:
    adverse = {
        "SPY": _series("SPY", "600", "-0.1"),
        "VIXY": _series("VIXY", "20", "0.1"),
        "UUP": _series("UUP", "20", "0.05"),
        "TLT": _series("TLT", "120", "-0.05"),
        "IEF": _series("IEF", "115", "-0.04"),
    }
    shock = dict(adverse)
    spy = list(shock["SPY"])
    spy[-1] = spy[-1].model_copy(update={"close": spy[-1].close - Decimal("20")})
    shock["SPY"] = tuple(spy)

    assert classify_macro_regime(adverse).regime is MacroRegime.RISK_OFF
    assert classify_macro_regime(shock).regime is MacroRegime.SHOCK
