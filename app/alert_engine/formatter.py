"""Readable, information-rich rendering for human-only local alerts."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, cast

from app.contracts import AlertKind, AnalysisHorizon, AnalysisResult, LocalAlert

from .confirmed import BuyMaturity, buy_maturity

_HORIZON_LABELS = {
    AnalysisHorizon.LONG_TERM: "LONG",
    AnalysisHorizon.SWING: "SWING",
    AnalysisHorizon.INTRADAY: "INTRADAY",
    AnalysisHorizon.DILUTION: "SEC",
    AnalysisHorizon.VOLUME_STRUCTURE: "VOLUME",
    AnalysisHorizon.OPTIONS_GAMMA: "GAMMA",
    AnalysisHorizon.NEWS: "NEWS",
}
_BUY_BANNER_STYLES = {
    BuyMaturity.TACTICAL_RECOVERY: "\x1b[1;30;103m",
    BuyMaturity.EARLY_ENTRY: "\x1b[1;30;103m",
    BuyMaturity.SWING_CONFIRMED: "\x1b[1;97;44m",
    BuyMaturity.HIGH_CONVICTION: "\x1b[1;30;102m",
    BuyMaturity.FULLY_MATURED: "\x1b[1;97;45m",
}
_BUY_LABELS = {
    BuyMaturity.TACTICAL_RECOVERY: "TACTICAL RECOVERY",
    BuyMaturity.EARLY_ENTRY: "EARLY PARTIAL ENTRY",
    BuyMaturity.SWING_CONFIRMED: "SWING CONFIRMED",
    BuyMaturity.HIGH_CONVICTION: "HIGH CONVICTION",
    BuyMaturity.FULLY_MATURED: "FULLY MATURED",
}
_PROTECT_BANNER_STYLE = "\x1b[1;97;41m"
_NEWS_RISK_BUY_BANNER_STYLE = "\x1b[1;97;41m"
_FLOW_BUY_BANNER_STYLE = "\x1b[1;30;103m"
# Clear inherited reverse/background attributes before applying foreground color.
_EARLY_INTRADAY_BANNER_STYLE = "\x1b[0;27;49;1;93m"
_ENTRY_WATCH_BANNER_STYLE = "\x1b[0;27;49;1;93m"
_SWING_SETUP_BANNER_STYLE = "\x1b[0;27;49;1;96m"
_LONG_BUY_ZONE_BANNER_STYLE = "\x1b[0;27;49;1;92m"
_OPPORTUNITY_PROGRESS_STYLES = {
    "ARMED": "\x1b[1;97;44m",
    "IN_ZONE": "\x1b[1;30;103m",
    "L1": "\x1b[1;30;103m",
    "L2": "\x1b[1;97;44m",
    "L3": "\x1b[1;30;102m",
    "L4": "\x1b[1;97;45m",
}
_DEFAULT_OPPORTUNITY_PROGRESS_STYLE = "\x1b[1;97;44m"
_OPPORTUNITY_CLOSED_STYLE = "\x1b[1;97;41m"
_RESET_STYLE = "\x1b[0m"


def format_local_alert(alert: LocalAlert, *, color: bool = False) -> str:
    """Render an alert with levels, technical context, and evidence."""

    analyses = {item.horizon: item for item in alert.component_analyses}
    lines: list[str] = []
    if alert.kind is AlertKind.PORTFOLIO_PROTECT:
        banner = f"PROTECT {alert.symbol}"
        lines.append(f"{_PROTECT_BANNER_STYLE} {banner} {_RESET_STYLE}" if color else banner)
    if alert.kind is AlertKind.PORTFOLIO_FLOW_BUY:
        price = _metrics(alert).get("current_price")
        banner = f"{alert.symbol} | BUY FLOW {_money(price)} | AGGRESSIVE ENTRY WATCH"
        lines.append(f"{_FLOW_BUY_BANNER_STYLE} {banner} {_RESET_STYLE}" if color else banner)
    if alert.kind is AlertKind.EARLY_INTRADAY_WITHOUT_CONFIRMATION:
        values = _metrics(alert)
        price = values.get("current_price")
        banner = (
            f"{alert.symbol} | EARLY INTRADAY {_money(price)} | "
            "WITHOUT CONFIRMATION"
        )
        lines.append(
            f"{_EARLY_INTRADAY_BANNER_STYLE} {banner} {_RESET_STYLE}"
            if color
            else banner
        )
    watch_banner = _candidate_watch_banner(alert, analyses)
    if watch_banner is not None:
        style, banner = watch_banner
        lines.append(f"{style} {banner} {_RESET_STYLE}" if color else banner)
    if alert.kind is AlertKind.ENTRY_OPPORTUNITY_PROGRESS:
        values = _metrics(alert)
        progress = _number(values.get("progress_percent"))
        maturity = values.get("maturity", "-")
        banner = f"{alert.symbol} | ENTRY PROGRESS {progress}% | {maturity}"
        style = _entry_progress_style(maturity)
        lines.append(
            f"{style} {banner} {_RESET_STYLE}" if color else banner
        )
    if alert.kind is AlertKind.ENTRY_OPPORTUNITY_CLOSED:
        banner = f"{alert.symbol} | PAPER TRADE CLOSED | REVIEW GAIN/LOSS"
        lines.append(
            f"{_OPPORTUNITY_CLOSED_STYLE} {banner} {_RESET_STYLE}" if color else banner
        )
    banner_result = _buy_banner(alert, analyses)
    if banner_result is not None:
        maturity, buy_banner = banner_result
        style = (
            _NEWS_RISK_BUY_BANNER_STYLE
            if _metrics(alert).get("news_risk_active") is True
            else _BUY_BANNER_STYLES[maturity]
        )
        lines.append(
            f"{style} {buy_banner} {_RESET_STYLE}" if color else buy_banner
        )
    lines.extend(
        [
            f"[{alert.severity.value}] {alert.symbol} score={_number(alert.score)} {alert.title}",
            f"  Decision: {alert.message}",
        ]
    )
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
) -> tuple[BuyMaturity, str] | None:
    maturity = buy_maturity(alert)
    if maturity is None:
        return None
    alert_metrics = _metrics(alert)
    confirmed_price = _first(
        alert_metrics.get("current_price"),
        *(
            _metrics(analysis).get("reference_price")
            for horizon in (
                AnalysisHorizon.INTRADAY,
                AnalysisHorizon.SWING,
                AnalysisHorizon.LONG_TERM,
            )
            if (analysis := analyses.get(horizon)) is not None
        ),
    )
    if confirmed_price is None:
        return None
    level = maturity.value.split("_", maxsplit=1)[0]
    banner = f"{alert.symbol} | BUY {level} {_money(confirmed_price)} | {_BUY_LABELS[maturity]}"
    return maturity, banner


def _candidate_watch_banner(
    alert: LocalAlert,
    analyses: dict[AnalysisHorizon, AnalysisResult],
) -> tuple[str, str] | None:
    price = _first(
        _metrics(alert).get("current_price"),
        *(
            _metrics(analysis).get("reference_price")
            for horizon in (
                AnalysisHorizon.INTRADAY,
                AnalysisHorizon.SWING,
                AnalysisHorizon.LONG_TERM,
            )
            if (analysis := analyses.get(horizon)) is not None
        ),
    )
    if price is None:
        return None
    if alert.kind is AlertKind.ENTRY_WATCH:
        title = alert.title.upper()
        if "BREAKAWAY WATCH" in title:
            return (
                _ENTRY_WATCH_BANNER_STYLE,
                f"{alert.symbol} | BREAKAWAY WATCH | ENTRY CANDIDATE {_money(price)}",
            )
        if "IN_ZONE" in title:
            return (
                _ENTRY_WATCH_BANNER_STYLE,
                f"{alert.symbol} | IN ZONE | ENTRY CANDIDATE {_money(price)}",
            )
    if alert.kind is AlertKind.SWING_SETUP:
        return (
            _SWING_SETUP_BANNER_STYLE,
            f"{alert.symbol} | SWING SETUP | ENTRY CANDIDATE {_money(price)}",
        )
    if alert.kind is AlertKind.LONG_BUY_ZONE:
        return (
            _LONG_BUY_ZONE_BANNER_STYLE,
            f"{alert.symbol} | LONG BUY ZONE | ENTRY CANDIDATE {_money(price)}",
        )
    return None


def _entry_progress_style(maturity: object) -> str:
    value = getattr(maturity, "value", maturity)
    return _OPPORTUNITY_PROGRESS_STYLES.get(
        str(value).upper(), _DEFAULT_OPPORTUNITY_PROGRESS_STYLE
    )


def _level_line(
    alert: LocalAlert,
    analyses: dict[AnalysisHorizon, AnalysisResult],
) -> str | None:
    alert_metrics = _metrics(alert)
    analysis_metrics = [_metrics(item) for item in analyses.values()]
    price = _first(
        alert_metrics.get("current_price"),
        *(values.get("reference_price") for values in reversed(analysis_metrics)),
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
    if alert.kind is AlertKind.SWING_SETUP:
        objective_label = "Resistance"
        objective = _first(
            *(values.get("resistance") for values in analysis_metrics),
        )
    else:
        objective_label = "Objective"
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
        fields.append(f"{objective_label} {_money(objective)}")
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
    risk = _optional_number(metrics.get("risk_percent"))
    _append(fields, "Risk to invalidation", f"{risk}%" if risk is not None else None)
    reward_risk = _optional_number(metrics.get("reward_risk_to_resistance"))
    _append(
        fields,
        "R/R to resistance",
        f"{reward_risk}R" if reward_risk is not None else None,
    )
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
