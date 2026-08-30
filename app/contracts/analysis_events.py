"""Versioned event names and NATS subjects for the analytical pipeline."""

from typing import Final

from .enums import (
    AlertSeverity,
    AnalysisHorizon,
    BarTimeframe,
    EntryOpportunityStatus,
    EntrySignalFamily,
    EntryWatchStatus,
)

MARKET_BAR_EVENT: Final = "market.bar.received"
MARKET_BAR_UPDATED_EVENT: Final = "market.bar.updated"
ANALYSIS_RESULT_EVENT: Final = "analysis.result.produced"
LOCAL_ALERT_EVENT: Final = "alert.local.produced"
SERVICE_HEALTH_EVENT: Final = "service.health.reported"
ENTRY_WATCH_TRANSITION_EVENT: Final = "entry-watch.transitioned"
ENTRY_OPPORTUNITY_EVENT: Final = "entry-opportunity.updated"
ENTRY_SIGNAL_EVENT: Final = "entry-signal.confirmed"
ENTRY_SETUP_ASSESSMENT_EVENT: Final = "entry-setup.assessed"
PATREON_CAPS_ASSESSMENT_EVENT: Final = "patreon-caps.assessed"
PATREON_CAPS_TRANSITION_EVENT: Final = "patreon-caps.transitioned"
ELLIOTT_WAVE_ASSESSMENT_EVENT: Final = "elliott-wave.assessed"
SUPPORT_ASSESSMENT_EVENT: Final = "support-confirmation.assessed"
SUPPORT_TRANSITION_EVENT: Final = "support-confirmation.transitioned"
GERI_ASSESSMENT_EVENT: Final = "4hgeri.assessed"
GERI_TRANSITION_EVENT: Final = "4hgeri.transitioned"
SWING_TRADE_ASSESSMENT_EVENT: Final = "swing-trade.assessed"
SWING_TRADE_TRANSITION_EVENT: Final = "swing-trade.transitioned"
OPTIONS_GAMMA_ASSESSMENT_EVENT: Final = "options-gamma.assessed"
LEVERAGED_THESIS_ASSESSMENT_EVENT: Final = "leveraged-thesis.assessed"
LEVERAGED_THESIS_TRANSITION_EVENT: Final = "leveraged-thesis.transitioned"
UNIVERSE_CHANGED_EVENT: Final = "universe.changed"
FUSION_ASSESSMENT_EVENT: Final = "signal-fusion.assessed"
FUSION_TRANSITION_EVENT: Final = "signal-fusion.transitioned"
FUSION_BUY_CONFIRMED_EVENT: Final = "signal-fusion.buy-confirmed"
FUSION_RECOVERY_CONFIRMED_EVENT: Final = "signal-fusion.recovery-confirmed"
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


def universe_changed_subject() -> str:
    return "marketbot.v1.universe.changed.core"


def entry_watch_transition_subject(status: EntryWatchStatus, symbol: str) -> str:
    return f"marketbot.v1.entry-watch.transition.{status.value}.{_symbol_token(symbol)}"


def entry_opportunity_subject(status: EntryOpportunityStatus, symbol: str) -> str:
    return f"marketbot.v1.entry-opportunity.transition.{status.value}.{_symbol_token(symbol)}"


def entry_signal_subject(family: EntrySignalFamily, symbol: str) -> str:
    return f"marketbot.v1.entry-signal.{family.value}.{_symbol_token(symbol)}"


def entry_setup_assessment_subject(family: EntrySignalFamily, symbol: str) -> str:
    return f"marketbot.v1.entry-setup.{family.value}.{_symbol_token(symbol)}"


def patreon_caps_assessment_subject(symbol: str) -> str:
    return f"marketbot.v1.patreon-caps.assessment.{_symbol_token(symbol)}"


def patreon_caps_transition_subject(status: object, symbol: str) -> str:
    value = getattr(status, "value", status)
    if not isinstance(value, str) or not value:
        raise ValueError("PatreonCaps state is invalid")
    return f"marketbot.v1.patreon-caps.transition.{value}.{_symbol_token(symbol)}"


def elliott_wave_assessment_subject(symbol: str) -> str:
    return f"marketbot.v1.elliott-wave.assessment.{_symbol_token(symbol)}"


def support_assessment_subject(symbol: str) -> str:
    return f"marketbot.v1.support-confirmation.assessment.{_symbol_token(symbol)}"


def support_transition_subject(state: object, symbol: str) -> str:
    value = getattr(state, "value", state)
    if not isinstance(value, str) or not value:
        raise ValueError("support state is invalid")
    return f"marketbot.v1.support-confirmation.transition.{value}.{_symbol_token(symbol)}"


def geri_assessment_subject(symbol: str) -> str:
    return f"marketbot.v1.4hgeri.assessment.{_symbol_token(symbol)}"


def geri_transition_subject(maturity: object, symbol: str) -> str:
    value = getattr(maturity, "value", maturity)
    if not isinstance(value, str) or not value:
        raise ValueError("4HGERI maturity is invalid")
    return f"marketbot.v1.4hgeri.transition.{value}.{_symbol_token(symbol)}"


def swing_trade_assessment_subject(symbol: str) -> str:
    return f"marketbot.v1.swing-trade.assessment.{_symbol_token(symbol)}"


def swing_trade_transition_subject(maturity: object | None, symbol: str) -> str:
    value = getattr(maturity, "value", maturity) or "NO_THESIS"
    if not isinstance(value, str):
        raise ValueError("SwingTrade maturity is invalid")
    return f"marketbot.v1.swing-trade.transition.{value}.{_symbol_token(symbol)}"


def options_gamma_assessment_subject(symbol: str) -> str:
    return f"marketbot.v1.options-gamma.assessment.{_symbol_token(symbol)}"


def leveraged_thesis_assessment_subject(symbol: str) -> str:
    return f"marketbot.v1.leveraged-thesis.assessment.{_symbol_token(symbol)}"


def leveraged_thesis_transition_subject(state: object, symbol: str) -> str:
    value = getattr(state, "value", state)
    if not isinstance(value, str) or not value:
        raise ValueError("leveraged thesis state is invalid")
    return f"marketbot.v1.leveraged-thesis.transition.{value}.{_symbol_token(symbol)}"


def fusion_assessment_subject(symbol: str) -> str:
    return f"marketbot.v1.signal-fusion.assessment.{_symbol_token(symbol)}"


def fusion_transition_subject(state: object, symbol: str) -> str:
    value = getattr(state, "value", state)
    if not isinstance(value, str) or not value:
        raise ValueError("fusion state is invalid")
    return f"marketbot.v1.signal-fusion.transition.{value}.{_symbol_token(symbol)}"


def fusion_buy_confirmed_subject(symbol: str) -> str:
    return f"marketbot.v1.signal-fusion.buy-confirmed.{_symbol_token(symbol)}"


def fusion_recovery_confirmed_subject(symbol: str) -> str:
    return f"marketbot.v1.signal-fusion.recovery-confirmed.{_symbol_token(symbol)}"


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
