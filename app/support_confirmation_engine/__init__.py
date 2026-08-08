"""Public surface of the independent Support Confirmation analysis engine."""

from .engine import SupportConfirmationEngine
from .models import SupportContext, SupportZoneHint

__all__ = ["SupportConfirmationEngine", "SupportContext", "SupportZoneHint"]
