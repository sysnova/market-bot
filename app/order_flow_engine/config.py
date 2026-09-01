"""Strict strategy loading for the bounded Order Flow symbol scope."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, cast

import yaml

from .engine import OrderFlowPolicy


def load_order_flow_policy(path: Path) -> OrderFlowPolicy:
    """Load the fixed microstructure scope from one immutable rule artifact."""

    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Order Flow strategy must be a mapping")
    raw = cast("dict[str, object]", payload)
    symbols = raw.get("tracked_symbols")
    if not isinstance(symbols, list) or not symbols:
        raise ValueError("Order Flow strategy requires tracked_symbols")
    values = cast("list[object]", symbols)
    if any(not isinstance(symbol, str) for symbol in values):
        raise ValueError("Order Flow tracked_symbols must contain strings")
    behavior = raw.get("behavior", {})
    if not isinstance(behavior, dict):
        raise ValueError("Order Flow behavior must be a mapping")
    configured = cast("dict[str, object]", behavior)
    kwargs: dict[str, Any] = {
        "tracked_symbols": tuple(cast("list[str]", values)),
    }
    decimal_fields = (
        "minimum_volume",
        "pressure_ratio",
        "large_trade_size",
        "absorption_max_price_change_bps",
        "divergence_minimum_price_change_bps",
        "transition_confirmation_seconds",
        "reversal_confirmation_seconds",
        "neutral_confirmation_seconds",
    )
    integer_fields = (
        "minimum_trades",
        "absorption_minimum_trades",
        "transition_confirmation_samples",
        "reversal_confirmation_samples",
        "neutral_confirmation_samples",
    )
    for name in decimal_fields:
        if name in configured:
            kwargs[name] = _decimal(configured[name], name=name)
    for name in integer_fields:
        if name in configured:
            kwargs[name] = _integer(configured[name], name=name)
    if "quote_max_age_seconds" in configured:
        seconds = _decimal(configured["quote_max_age_seconds"], name="quote_max_age_seconds")
        kwargs["quote_max_age"] = timedelta(
            microseconds=int(seconds * Decimal("1000000"))
        )
    return OrderFlowPolicy(**kwargs)


def _decimal(value: object, *, name: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"Order Flow behavior {name} must be decimal")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"Order Flow behavior {name} must be decimal") from error


def _integer(value: object, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"Order Flow behavior {name} must be integer")
    return value
