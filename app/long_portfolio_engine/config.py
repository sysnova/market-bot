"""Strict loader for exact-version long-portfolio rule artifacts."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast

import yaml

from .models import LongPortfolioPolicy, PortfolioAllocation


def load_long_portfolio_policy(
    path: Path, *, allocations: tuple[PortfolioAllocation, ...]
) -> LongPortfolioPolicy:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("long-portfolio artifact must be a mapping")
    raw = cast("dict[str, object]", payload)
    thresholds = _mapping(raw, "thresholds")
    timing = _mapping(raw, "timing")
    return LongPortfolioPolicy(
        rule_version=str(raw["rule_version"]),
        horizon_end=str(raw["horizon_end"]),
        portfolio_capital_usd=Decimal(str(raw["portfolio_capital_usd"])),
        cash_weight_percent=Decimal(str(raw["cash_weight_percent"])),
        reserved_weight_percent=Decimal(str(raw["reserved_weight_percent"])),
        allocations=allocations,
        minimum_score=Decimal(str(thresholds["minimum_score"])),
        minimum_confidence=Decimal(str(thresholds["minimum_confidence"])),
        minimum_setup_score=Decimal(str(thresholds["minimum_setup_score"])),
        minimum_entry_score=Decimal(str(thresholds["minimum_entry_score"])),
        minimum_trend_template_score=Decimal(
            str(thresholds["minimum_trend_template_score"])
        ),
        minimum_qualified_sessions=int(str(thresholds["minimum_qualified_sessions"])),
        initial_tranche_percent=Decimal(str(raw["initial_tranche_percent"])),
        maximum_signal_age=timedelta(hours=int(str(timing["maximum_signal_age_hours"]))),
        cooldown=timedelta(days=int(str(timing["cooldown_days"]))),
        alert_ttl=timedelta(days=int(str(timing["alert_ttl_days"]))),
        allowed_market_regimes=_strings(thresholds, "allowed_market_regimes"),
        blocked_risk_flags=_strings(thresholds, "blocked_risk_flags"),
    )


def _mapping(values: dict[str, object], key: str) -> dict[str, object]:
    value = values[key]
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be a mapping")
    return cast("dict[str, object]", value)


def _strings(values: dict[str, object], key: str) -> tuple[str, ...]:
    value = values[key]
    if not isinstance(value, list):
        raise ValueError(f"{key} must be a string list")
    items = cast("list[object]", value)
    if not all(isinstance(item, str) for item in items):
        raise ValueError(f"{key} must be a string list")
    return tuple(str(item) for item in items)
