"""Stable event names and NATS subjects for the microstructure pipeline."""

from __future__ import annotations

import re
from typing import Final

MARKET_TRADE_EVENT: Final = "market.trade.received"
MARKET_QUOTE_EVENT: Final = "market.quote.received"
MARKET_TRADE_CORRECTION_EVENT: Final = "market.trade.corrected"
MARKET_TRADE_CANCEL_EVENT: Final = "market.trade.cancelled"

ORDER_FLOW_STATE_EVENT: Final = "order-flow.state.assessed"
ORDER_FLOW_TRANSITION_EVENT: Final = "order-flow.state.transitioned"
ORDER_FLOW_SUPPORT_ASSESSMENT_EVENT: Final = "order-flow.support.assessed"


def order_flow_state_subject(symbol: str) -> str:
    return f"marketbot.v1.order-flow.state.{_token(symbol)}"


def order_flow_transition_subject(state: object, symbol: str) -> str:
    return f"marketbot.v1.order-flow.transition.{_token(str(state))}.{_token(symbol)}"


def order_flow_support_subject(symbol: str) -> str:
    return f"marketbot.v1.order-flow.support.{_token(symbol)}"


def _token(value: str) -> str:
    token = re.sub(r"[^A-Z0-9_-]+", "_", value.strip().upper()).strip("_")
    if not token:
        raise ValueError("value cannot form a NATS subject token")
    return token
