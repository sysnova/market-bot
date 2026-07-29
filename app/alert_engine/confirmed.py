"""Classification used by the confirmed-buy console."""

from app.contracts import AlertKind, LocalAlert


def is_confirmed_buy(alert: LocalAlert) -> bool:
    """Return whether an alert represents an actionable confirmed long entry."""

    if alert.kind in {AlertKind.ENTRY_CONFIRMED, AlertKind.HIGH_CONVICTION_BUY}:
        return True
    return alert.kind is AlertKind.ENTRY_WATCH and "ENTRY TRIGGERED" in alert.title.upper()
