"""Exact-version PatreonCaps YAML loader."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import yaml

from app.contracts import MacroRegime

from .models import PatreonCapsPolicy


def load_patreon_caps_policy(path: Path) -> PatreonCapsPolicy:
    document = cast("dict[str, Any]", yaml.safe_load(path.read_text(encoding="utf-8")))
    thresholds = cast("dict[str, Any]", document["thresholds"])
    timing = cast("dict[str, Any]", document["timing"])
    macro = cast("dict[str, Any]", document["macro"])
    lesson = cast("dict[str, Any]", document.get("lesson", {}))
    scoring = cast("dict[str, Any]", document.get("scoring", {}))
    return PatreonCapsPolicy(
        rule_version=str(document["rule_version"]),
        portfolio_capital_usd=Decimal(str(document["portfolio_capital_usd"])),
        minimum_confluence_score=Decimal(str(thresholds["minimum_confluence_score"])),
        minimum_source_families=int(thresholds["minimum_source_families"]),
        minimum_confirmation_score=Decimal(str(thresholds["minimum_confirmation_score"])),
        cluster_distance_atr=Decimal(str(thresholds["cluster_distance_atr"])),
        cluster_width_atr=Decimal(str(thresholds["cluster_width_atr"])),
        zone_padding_atr=Decimal(str(thresholds["zone_padding_atr"])),
        test_padding_atr=Decimal(str(thresholds["test_padding_atr"])),
        invalidation_buffer_atr=Decimal(str(thresholds["invalidation_buffer_atr"])),
        defense_distance_atr=Decimal(str(thresholds["defense_distance_atr"])),
        v_rvol_minimum=Decimal(str(thresholds["v_rvol_minimum"])),
        base_rvol_minimum=Decimal(str(thresholds["base_rvol_minimum"])),
        impulse_minimum_atr=Decimal(str(thresholds["impulse_minimum_atr"])),
        watch_ttl=timedelta(days=int(timing["watch_ttl_days"])),
        long_max_age=timedelta(hours=int(timing["long_max_age_hours"])),
        swing_max_age=timedelta(hours=int(timing["swing_max_age_hours"])),
        intraday_max_age=timedelta(minutes=int(timing["intraday_max_age_minutes"])),
        macro_thresholds={
            MacroRegime(name): Decimal(str(value))
            for name, value in cast("dict[str, Any]", macro["buy_thresholds"]).items()
        },
        macro_symbols=tuple(str(item) for item in cast("list[object]", macro["symbols"])),
        lesson_enabled=bool(lesson.get("enabled", False)),
        require_daily_above_sma200=bool(
            lesson.get("require_daily_above_sma200", False)
        ),
        cross_lookback_bars=int(lesson.get("cross_lookback_bars", 20)),
        triangle_lookback_bars=int(lesson.get("triangle_lookback_bars", 80)),
        triangle_tolerance_atr=Decimal(
            str(lesson.get("triangle_tolerance_atr", "0.35"))
        ),
        wave_0618_tolerance_atr=Decimal(
            str(lesson.get("wave_0618_tolerance_atr", "0.15"))
        ),
        confluence_weight=Decimal(str(scoring.get("confluence_weight", "0.40"))),
        confirmation_weight=Decimal(str(scoring.get("confirmation_weight", "0.30"))),
        alignment_weight=Decimal(str(scoring.get("alignment_weight", "0.30"))),
        lesson_weight=Decimal(str(scoring.get("lesson_weight", "0"))),
    )


def default_policy() -> PatreonCapsPolicy:
    return PatreonCapsPolicy(
        rule_version="1.0.0",
        portfolio_capital_usd=Decimal("103000"),
        minimum_confluence_score=Decimal("65"),
        minimum_source_families=3,
        minimum_confirmation_score=Decimal("70"),
        cluster_distance_atr=Decimal("0.35"),
        cluster_width_atr=Decimal("0.75"),
        zone_padding_atr=Decimal("0.10"),
        test_padding_atr=Decimal("0.15"),
        invalidation_buffer_atr=Decimal("0.75"),
        defense_distance_atr=Decimal("0.25"),
        v_rvol_minimum=Decimal("1.5"),
        base_rvol_minimum=Decimal("1.2"),
        impulse_minimum_atr=Decimal("2"),
        watch_ttl=timedelta(days=56),
        long_max_age=timedelta(days=7),
        swing_max_age=timedelta(hours=8),
        intraday_max_age=timedelta(minutes=30),
        macro_thresholds={
            MacroRegime.RISK_ON: Decimal("75"),
            MacroRegime.NEUTRAL: Decimal("80"),
            MacroRegime.RISK_OFF: Decimal("85"),
        },
        macro_symbols=("UUP", "VIXY", "TLT", "IEF", "SPY"),
    )
