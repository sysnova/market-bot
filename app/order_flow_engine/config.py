"""Strict strategy loading for the bounded Order Flow symbol scope."""

from __future__ import annotations

from pathlib import Path
from typing import cast

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
    return OrderFlowPolicy(tracked_symbols=tuple(cast("list[str]", values)))
