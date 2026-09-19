"""Recover transiently redelivered confirmed SHORT alerts without losing their audit."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.contracts import LocalAlert

from .v22 import EntryOpportunityEngineV22

_NEW_YORK = ZoneInfo("America/New_York")


class EntryOpportunityEngineV23(EntryOpportunityEngineV22):
    """Evaluate bounded same-session redeliveries at the alert's original timestamp."""

    engine_version = "23.0.0"

    def __init__(
        self,
        *args: object,
        short_alert_delivery_grace: timedelta = timedelta(hours=6),
        **kwargs: object,
    ) -> None:
        super().__init__(*args, **kwargs)  # pyright: ignore[reportArgumentType]
        if short_alert_delivery_grace <= timedelta():
            raise ValueError("SHORT alert delivery grace must be positive")
        self._short_alert_delivery_grace = short_alert_delivery_grace

    def _short_alert_evaluation_time(self, alert: LocalAlert) -> datetime:
        delivered_at = self._now()
        age = delivered_at - alert.created_at
        same_session_date = (
            delivered_at.astimezone(_NEW_YORK).date()
            == alert.created_at.astimezone(_NEW_YORK).date()
        )
        if timedelta() <= age <= self._short_alert_delivery_grace and same_session_date:
            return alert.created_at
        return delivered_at
