"""Progressive LONG recovery with independently identified continuation entries."""

from app.contracts import NamedValue

from .models import Swing4HGeriContext
from .recovery_progression import progressive_recovery_metrics
from .v19 import Swing4HGeriEngineV19


class Swing4HGeriEngineV110(Swing4HGeriEngineV19):
    engine_version = "1.10.0"

    def _recovery_metrics(self, context: Swing4HGeriContext) -> tuple[NamedValue, ...]:
        return progressive_recovery_metrics(context, self._recovery)
