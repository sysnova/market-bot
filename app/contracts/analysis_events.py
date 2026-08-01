"""Versioned event names and NATS subjects for the analytical pipeline."""

from typing import Final

from .enums import AlertSeverity, AnalysisHorizon, BarTimeframe, EntryWatchStatus

MARKET_BAR_EVENT: Final = "market.bar.received"
MARKET_BAR_UPDATED_EVENT: Final = "market.bar.updated"
ANALYSIS_RESULT_EVENT: Final = "analysis.result.produced"
LOCAL_ALERT_EVENT: Final = "alert.local.produced"
SERVICE_HEALTH_EVENT: Final = "service.health.reported"
ENTRY_WATCH_TRANSITION_EVENT: Final = "entry-watch.transitioned"
PATREON_CAPS_ASSESSMENT_EVENT: Final = "patreon-caps.assessed"
PATREON_CAPS_TRANSITION_EVENT: Final = "patreon-caps.transitioned"
MARKET_ROTATION_EVENT: Final = "market-rotation.analyzed"
MARKET_ROTATION_SUBJECT: Final = "marketbot.v1.rotation.result"


def market_bar_subject(timeframe: BarTimeframe, symbol: str) -> str:
    return f"marketbot.v1.market.bar.{timeframe.value}.{_symbol_token(symbol)}"


def analysis_result_subject(horizon: AnalysisHorizon, symbol: str) -> str:
    return f"marketbot.v1.analysis.result.{horizon.value}.{_symbol_token(symbol)}"


def local_alert_subject(severity: AlertSeverity, symbol: str) -> str:
    return f"marketbot.v1.alert.local.{severity.value}.{_symbol_token(symbol)}"


def service_health_subject(service: str) -> str:
    return f"marketbot.v1.service.health.{_service_token(service)}"


def entry_watch_transition_subject(status: EntryWatchStatus, symbol: str) -> str:
    return f"marketbot.v1.entry-watch.transition.{status.value}.{_symbol_token(symbol)}"


def patreon_caps_assessment_subject(symbol: str) -> str:
    return f"marketbot.v1.patreon-caps.assessment.{_symbol_token(symbol)}"


def patreon_caps_transition_subject(status: object, symbol: str) -> str:
    value = getattr(status, "value", status)
    if not isinstance(value, str) or not value:
        raise ValueError("PatreonCaps state is invalid")
    return f"marketbot.v1.patreon-caps.transition.{value}.{_symbol_token(symbol)}"


def _symbol_token(symbol: str) -> str:
    normalized = symbol.strip().upper()
    if (
        not normalized
        or len(normalized) > 16
        or not normalized[0].isalnum()
        or any(not character.isalnum() and character not in ".-" for character in normalized)
    ):
        raise ValueError("symbol is not safe for an event subject")
    return normalized.replace(".", "_")


def _service_token(service: str) -> str:
    normalized = service.strip().lower()
    if (
        not normalized
        or len(normalized) > 64
        or not normalized[0].isalnum()
        or any(not character.isalnum() and character not in "-_" for character in normalized)
    ):
        raise ValueError("service is not safe for an event subject")
    return normalized
