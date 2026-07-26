"""Versioned event names and NATS subjects for the analytical pipeline."""

from typing import Final

from .enums import AlertSeverity, AnalysisHorizon, BarTimeframe

MARKET_BAR_EVENT: Final = "market.bar.received"
MARKET_BAR_UPDATED_EVENT: Final = "market.bar.updated"
ANALYSIS_RESULT_EVENT: Final = "analysis.result.produced"
LOCAL_ALERT_EVENT: Final = "alert.local.produced"


def market_bar_subject(timeframe: BarTimeframe, symbol: str) -> str:
    return f"marketbot.v1.market.bar.{timeframe.value}.{_symbol_token(symbol)}"


def analysis_result_subject(horizon: AnalysisHorizon, symbol: str) -> str:
    return f"marketbot.v1.analysis.result.{horizon.value}.{_symbol_token(symbol)}"


def local_alert_subject(severity: AlertSeverity, symbol: str) -> str:
    return f"marketbot.v1.alert.local.{severity.value}.{_symbol_token(symbol)}"


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
