"""Buy maturity classification used by focused human monitors."""

from enum import StrEnum

from app.contracts import AlertKind, AnalysisHorizon, LocalAlert

_TACTICAL_HORIZONS = {AnalysisHorizon.LONG_TERM, AnalysisHorizon.INTRADAY}
_SWING_HORIZONS = {AnalysisHorizon.SWING, AnalysisHorizon.INTRADAY}
_CONVICTION_HORIZONS = {
    AnalysisHorizon.LONG_TERM,
    AnalysisHorizon.SWING,
    AnalysisHorizon.INTRADAY,
}


class BuyMaturity(StrEnum):
    """Explicit progression from tactical recovery to fully matured evidence."""

    TACTICAL_RECOVERY = "L1_TACTICAL_RECOVERY"
    SWING_CONFIRMED = "L2_SWING_CONFIRMED"
    HIGH_CONVICTION = "L3_HIGH_CONVICTION"
    FULLY_MATURED = "L4_FULLY_MATURED"


def buy_maturity(alert: LocalAlert) -> BuyMaturity | None:
    """Classify a buy alert without collapsing distinct confirmation paths."""

    horizons = set(alert.horizons)
    if alert.kind is AlertKind.ENTRY_CONFIRMED:
        if _CONVICTION_HORIZONS.issubset(horizons):
            return BuyMaturity.HIGH_CONVICTION
        if _SWING_HORIZONS.issubset(horizons):
            return BuyMaturity.SWING_CONFIRMED
        if _TACTICAL_HORIZONS.issubset(horizons):
            return BuyMaturity.TACTICAL_RECOVERY
        return None
    if alert.kind is AlertKind.HIGH_CONVICTION_BUY:
        return (
            BuyMaturity.HIGH_CONVICTION
            if _CONVICTION_HORIZONS.issubset(horizons)
            else None
        )
    if alert.kind in {AlertKind.LONG_PORTFOLIO_BUY, AlertKind.PATREON_CAPS_BUY}:
        return BuyMaturity.FULLY_MATURED
    if (
        alert.kind is AlertKind.ENTRY_WATCH
        and "ENTRY TRIGGERED" in alert.title.upper()
        and _CONVICTION_HORIZONS.issubset(horizons)
    ):
        return BuyMaturity.FULLY_MATURED
    return None


def is_buy_alert(alert: LocalAlert) -> bool:
    """Return whether the alert belongs to any explicit buy maturity."""

    return buy_maturity(alert) is not None


def is_solid_buy(alert: LocalAlert) -> bool:
    """Return whether a buy reached high-conviction or fully-matured evidence."""

    return buy_maturity(alert) in {
        BuyMaturity.HIGH_CONVICTION,
        BuyMaturity.FULLY_MATURED,
    }


def is_confirmed_buy(alert: LocalAlert) -> bool:
    """Backward-compatible alias accepting every explicit buy maturity."""

    return is_buy_alert(alert)


def is_portfolio_monitor_alert(alert: LocalAlert) -> bool:
    """Return whether the focused monitor should render this alert."""

    return is_buy_alert(alert) or alert.kind is AlertKind.PORTFOLIO_PROTECT
