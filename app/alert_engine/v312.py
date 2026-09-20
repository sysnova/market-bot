"""V3.12: leave confirmed SHORT ownership to Leveraged Thesis."""

from datetime import datetime

from app.contracts import LocalAlert

from .v311 import AlertEngineV311


class AlertEngineV312(AlertEngineV311):
    """Preserve Alert behavior while making SHORT confirmation impossible here."""

    engine_version = "3.12.0"
    requires_order_flow_short_scope = False

    def _confirm_short(
        self, symbol: str, *, now: datetime, existing: LocalAlert | None
    ) -> LocalAlert | None:
        return None
