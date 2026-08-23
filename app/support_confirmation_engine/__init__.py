"""Public surface of the independent Support Confirmation analysis engine."""

from .engine import SupportConfirmationEngine
from .enrichment import SupportContribution, classify_support_enrichment
from .models import SupportContext, SupportZoneHint
from .v03 import SupportConfirmationEngineV03

__all__ = [
    "SupportConfirmationEngine",
    "SupportConfirmationEngineV03",
    "SupportContext",
    "SupportContribution",
    "SupportZoneHint",
    "classify_support_enrichment",
]
