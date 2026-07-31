"""Classification used by the confirmed-buy console."""

from app.contracts import AlertKind, LocalAlert


def is_confirmed_buy(alert: LocalAlert) -> bool:
    """Return whether an alert represents an actionable confirmed long entry."""

    if alert.kind in {AlertKind.ENTRY_CONFIRMED, AlertKind.HIGH_CONVICTION_BUY}:
        return True
    return alert.kind is AlertKind.ENTRY_WATCH and "ENTRY TRIGGERED" in alert.title.upper()


def is_portfolio_monitor_alert(alert: LocalAlert) -> bool:
    """Return whether the focused monitor should render this alert."""

    return is_confirmed_buy(alert) or alert.kind in {
        AlertKind.PORTFOLIO_PROTECT,
        AlertKind.LONG_PORTFOLIO_BUY,
    }
