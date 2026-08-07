"""Strict loader for versioned Portfolio Flow strategies."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast

import yaml

from .engine import PortfolioFlowPolicy


def load_portfolio_flow_policy(path: Path) -> PortfolioFlowPolicy:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("portfolio-flow artifact must be a mapping")
    raw = cast("dict[str, object]", payload)
    thresholds = _mapping(raw, "thresholds")
    timing = _mapping(raw, "timing")
    return PortfolioFlowPolicy(
        window=timedelta(minutes=int(str(timing["window_minutes"]))),
        cooldown=timedelta(minutes=int(str(timing["cooldown_minutes"]))),
        minimum_trades=int(str(thresholds["minimum_trades"])),
        minimum_volume=Decimal(str(thresholds["minimum_volume"])),
        sell_ratio=Decimal(str(thresholds["sell_ratio"])),
        buy_ratio=Decimal(str(thresholds.get("buy_ratio", "1"))),
        minimum_drop_percent=Decimal(str(thresholds["minimum_drop_percent"])),
        minimum_rise_percent=Decimal(str(thresholds.get("minimum_rise_percent", "100"))),
        block_size=Decimal(str(thresholds["block_size"])),
    )


def _mapping(values: dict[str, object], key: str) -> dict[str, object]:
    value = values[key]
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be a mapping")
    return cast("dict[str, object]", value)
