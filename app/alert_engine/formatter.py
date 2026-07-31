"""Readable, information-rich rendering for human-only local alerts."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, cast

from app.contracts import AlertKind, AnalysisHorizon, AnalysisResult, LocalAlert

_HORIZON_LABELS = {
    AnalysisHorizon.LONG_TERM: "LONG",
    AnalysisHorizon.SWING: "SWING",
    AnalysisHorizon.INTRADAY: "INTRADAY",
    AnalysisHorizon.DILUTION: "SEC",
}
_BUYABLE_KINDS = {
    AlertKind.LONG_BUY_ZONE,
    AlertKind.ENTRY_CONFIRMED,
    AlertKind.HIGH_CONVICTION_BUY,
    AlertKind.LONG_PORTFOLIO_BUY,
}
_BUY_BANNER_STYLE = "\x1b[1;97;42m"
_PROTECT_BANNER_STYLE = "\x1b[1;97;41m"
_RESET_STYLE = "\x1b[0m"


def format_local_alert(alert: LocalAlert, *, color: bool = False) -> str:
    """Render an alert with levels, technical context, and evidence."""

    analyses = {item.horizon: item for item in alert.component_analyses}
    lines: list[str] = []
    if alert.kind is AlertKind.PORTFOLIO_PROTECT:
        banner = f"PROTECT {alert.symbol}"
        lines.append(
            f"{_PROTECT_BANNER_STYLE} {banner} {_RESET_STYLE}" if color else banner
        )
    buy_banner = _buy_banner(alert, analyses)
    if buy_banner is not None:
        lines.append(
            f"{_BUY_BANNER_STYLE} {buy_banner} {_RESET_STYLE}"
            if color
            else buy_banner
        )
    lines.extend([
        f"[{alert.severity.value}] {alert.symbol} score={_number(alert.score)} "
        f"{alert.title}",
        f"  Decision: {alert.message}",
    ])
    level_line = _level_line(alert, analyses)
    if level_line is not None:
        lines.append(f"  {level_line}")
    for horizon in (
        AnalysisHorizon.LONG_TERM,
        AnalysisHorizon.SWING,
        AnalysisHorizon.INTRADAY,
        AnalysisHorizon.DILUTION,
    ):
        analysis = analyses.get(horizon)
        if analysis is not None:
            lines.extend(_analysis_lines(analysis))
    if alert.reasons:
        lines.append(f"  Alert reasons: {'; '.join(alert.reasons)}")
    return "\n".join(lines)


def _buy_banner(
    alert: LocalAlert,
    analyses: dict[AnalysisHorizon, AnalysisResult],
) -> str | None:
    if alert.kind not in _BUYABLE_KINDS and not (
        alert.kind is AlertKind.ENTRY_WATCH and "ENTRY TRIGGERED" in alert.title.upper()
    ):
        return None
    alert_metrics = _metrics(alert)
    analysis_metrics = [_metrics(item) for item in analyses.values()]
    zone_low = _first(
        alert_metrics.get("buy_zone_low"),
        *(values.get("buy_zone_low") for values in analysis_metrics),
    )
    zone_high = _first(
        alert_metrics.get("buy_zone_high"),
        *(values.get("buy_zone_high") for values in analysis_metrics),
    )
    ideal_price = _midpoint(zone_low, zone_high)
    if ideal_price is None:
        ideal_price = _first(
            alert_metrics.get("current_price"),
            *(values.get("reference_price") for values in reversed(analysis_metrics)),
        )
    if ideal_price is None:
        return None
    return f"{alert.symbol} | IDEAL BUY {_money(ideal_price)}"


def _level_line(
    alert: LocalAlert,
    analyses: dict[AnalysisHorizon, AnalysisResult],
) -> str | None:
    alert_metrics = _metrics(alert)
    analysis_metrics = [_metrics(item) for item in analyses.values()]
    price = _first(
        alert_metrics.get("current_price"),
        *(
            values.get("reference_price")
            for values in reversed(analysis_metrics)
        ),
    )
    zone_low = _first(
        alert_metrics.get("buy_zone_low"),
        *(values.get("buy_zone_low") for values in analysis_metrics),
    )
    zone_high = _first(
        alert_metrics.get("buy_zone_high"),
        *(values.get("buy_zone_high") for values in analysis_metrics),
    )
    invalidation = _first(
        alert_metrics.get("invalidation"),
        alert_metrics.get("invalidation_level"),
        *(values.get("invalidation") for values in analysis_metrics),
        *(values.get("invalidation_level") for values in analysis_metrics),
    )
    objective = _first(
        alert_metrics.get("objective"),
        *(values.get("target_2r") for values in analysis_metrics),
        *(values.get("objective_level") for values in analysis_metrics),
    )
    fields: list[str] = []
    if price is not None:
        fields.append(f"Price {_money(price)}")
    if zone_low is not None and zone_high is not None:
        fields.append(f"Buy zone {_money(zone_low)} - {_money(zone_high)}")
    if invalidation is not None:
        fields.append(f"Invalidation {_money(invalidation)}")
    if objective is not None:
        fields.append(f"Objective {_money(objective)}")
    return " | ".join(fields) or None


def _analysis_lines(analysis: AnalysisResult) -> list[str]:
    metrics = _metrics(analysis)
    classification = _first(metrics.get("classification"), metrics.get("setup"))
    summary = (
        f"  {_HORIZON_LABELS[analysis.horizon]} "
        f"{analysis.verdict.value.lower()} / {analysis.direction.value.lower()} / "
        f"score {_number(analysis.score)}"
    )
    if classification is not None:
        summary += f" | {classification}"
    lines = [summary]
    technical = _technical_fields(analysis.horizon, metrics)
    if technical:
        lines.append(f"    {' | '.join(technical)}")
    lines.append(f"    Why: {'; '.join(analysis.reasons)}")
    return lines


def _technical_fields(horizon: AnalysisHorizon, metrics: dict[str, Any]) -> list[str]:
    if horizon is AnalysisHorizon.LONG_TERM:
        return _long_fields(metrics)
    if horizon is AnalysisHorizon.SWING:
        return _swing_fields(metrics)
    if horizon is AnalysisHorizon.INTRADAY:
        return _intraday_fields(metrics)
    return _sec_fields(metrics)


def _long_fields(metrics: dict[str, Any]) -> list[str]:
    fields: list[str] = []
    sma200 = metrics.get("weekly_sma200")
    distance = metrics.get("weekly_price_vs_sma200_percent")
    if sma200 is not None:
        suffix = f" ({_percent(distance)})" if distance is not None else ""
        fields.append(f"SMA200W {_money(sma200)}{suffix}")
    _append(
        fields,
        "RSI D/W",
        _pair(metrics.get("daily_rsi14"), metrics.get("weekly_rsi14")),
    )
    _append(
        fields,
        "RVOL D/W",
        _pair(
            metrics.get("daily_rvol20"),
            metrics.get("weekly_rvol10"),
            suffix="x",
        ),
    )
    _append(
        fields,
        "Setup/entry",
        _pair(metrics.get("setup_score"), metrics.get("entry_score")),
    )
    _append(fields, "Support", _money_or_none(metrics.get("support")))
    _append(fields, "Resistance", _money_or_none(metrics.get("resistance")))
    return fields


def _swing_fields(metrics: dict[str, Any]) -> list[str]:
    fields: list[str] = []
    _append(
        fields,
        "SMA20/50D",
        _money_pair(metrics.get("daily_sma20"), metrics.get("daily_sma50")),
    )
    _append(fields, "RSI14D", _optional_number(metrics.get("daily_rsi14")))
    _append(
        fields,
        "RVOL D/15m",
        _pair(
            metrics.get("daily_rvol20"),
            metrics.get("intraday_rvol20"),
            suffix="x",
        ),
    )
    pivot = metrics.get("pivot_low_avwap")
    pivot_distance = metrics.get("price_vs_pivot_low_avwap_percent")
    if pivot is not None:
        suffix = f" ({_percent(pivot_distance)})" if pivot_distance is not None else ""
        fields.append(f"Pivot AVWAP {_money(pivot)}{suffix}")
    breakout = metrics.get("breakout_avwap")
    breakout_distance = metrics.get("price_vs_breakout_avwap_percent")
    if breakout is not None:
        suffix = f" ({_percent(breakout_distance)})" if breakout_distance is not None else ""
        fields.append(f"Breakout AVWAP {_money(breakout)}{suffix}")
    _append(fields, "R/R", _ratio(metrics.get("risk_percent"), metrics.get("target_2r")))
    return fields


def _intraday_fields(metrics: dict[str, Any]) -> list[str]:
    fields: list[str] = []
    _append(fields, "VWAP", _money_or_none(metrics.get("session_vwap")))
    rvol = _optional_number(metrics.get("relative_volume"))
    _append(fields, "RVOL", f"{rvol}x" if rvol is not None else None)
    _append(fields, "EMA9/20", _money_pair(metrics.get("ema9"), metrics.get("ema20")))
    momentum = metrics.get("momentum_5_percent")
    _append(fields, "Momentum 5m", _percent(momentum) if momentum is not None else None)
    rr = _optional_number(metrics.get("reward_risk_ratio"))
    _append(fields, "R/R", f"{rr}R" if rr is not None else None)
    return fields


def _sec_fields(metrics: dict[str, Any]) -> list[str]:
    fields: list[str] = []
    _append(fields, "Risk", _optional_text(metrics.get("dilution_severity")))
    _append(fields, "Filings", _optional_number(metrics.get("analyzed_filing_count")))
    growth = metrics.get("share_growth_percent")
    _append(fields, "Share growth", _percent(growth) if growth is not None else None)
    runway = _optional_number(metrics.get("cash_runway_quarters"))
    _append(fields, "Cash runway", f"{runway}q" if runway is not None else None)
    evidence = metrics.get("evidence")
    if isinstance(evidence, list) and evidence:
        evidence_items = cast("list[object]", evidence)
        fields.append(f"Evidence {len(evidence_items)}")
    return fields


def _metrics(value: LocalAlert | AnalysisResult) -> dict[str, Any]:
    return {item.name: item.value for item in value.metrics}


def _append(fields: list[str], label: str, value: str | None) -> None:
    if value is not None:
        fields.append(f"{label} {value}")


def _first(*values: Any) -> Any:  # noqa: ANN401
    return next((value for value in values if value is not None), None)


def _number(value: object) -> str:
    if isinstance(value, Decimal):
        rendered = format(value, "f")
        return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered
    return str(value)


def _money(value: object) -> str:
    return f"${_number(value)}"


def _midpoint(low: object, high: object) -> Decimal | None:
    if isinstance(low, bool) or isinstance(high, bool):
        return None
    try:
        return (Decimal(str(low)) + Decimal(str(high))) / Decimal("2")
    except (ValueError, ArithmeticError):
        return None


def _money_or_none(value: object) -> str | None:
    return _money(value) if value is not None else None


def _percent(value: object) -> str:
    number = _number(value)
    return f"{number if number.startswith('-') else '+' + number}%"


def _optional_number(value: object) -> str | None:
    return _number(value) if value is not None else None


def _optional_text(value: object) -> str | None:
    return str(value) if value is not None else None


def _pair(left: object, right: object, *, suffix: str = "") -> str | None:
    if left is None and right is None:
        return None
    left_value = _number(left) if left is not None else "n/a"
    right_value = _number(right) if right is not None else "n/a"
    return f"{left_value}{suffix}/{right_value}{suffix}"


def _money_pair(left: object, right: object) -> str | None:
    if left is None and right is None:
        return None
    left_value = _money(left) if left is not None else "n/a"
    right_value = _money(right) if right is not None else "n/a"
    return f"{left_value}/{right_value}"


def _ratio(risk_percent: object, target: object) -> str | None:
    parts: list[str] = []
    if risk_percent is not None:
        parts.append(f"risk {_percent(risk_percent)}")
    if target is not None:
        parts.append(f"target {_money(target)}")
    return ", ".join(parts) or None
