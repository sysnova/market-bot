"""No-look-ahead outcome measurement for solid local buy alerts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from app.contracts import AnalysisHorizon, BarTimeframe, LocalAlert, MarketBar

from .confirmed import is_buy_alert

_NEW_YORK = ZoneInfo("America/New_York")
_FOUR_PLACES = Decimal("0.0001")
type FirstLevelHit = Literal["TARGET", "INVALIDATION", "AMBIGUOUS"]


@dataclass(frozen=True, slots=True)
class SolidBuyOutcome:
    """Deterministic markouts tied to one immutable solid-buy alert."""

    alert_id: UUID
    symbol: str
    alert_kind: str
    occurred_at: datetime
    entry_price: Decimal
    invalidation: Decimal | None
    target: Decimal | None
    first_level_hit: FirstLevelHit | None
    mfe_percent: Decimal | None
    mae_percent: Decimal | None
    return_15m: Decimal | None
    return_30m: Decimal | None
    return_60m: Decimal | None
    return_close: Decimal | None
    evaluated_through: datetime | None
    engine_versions: tuple[str, ...]
    entry_confirmation_rule_versions: tuple[str, ...]


def evaluate_solid_buy_outcomes(
    alerts: tuple[LocalAlert, ...],
    minute_bars: dict[str, tuple[MarketBar, ...]],
) -> tuple[SolidBuyOutcome, ...]:
    """Measure finalized bars strictly after each eligible alert timestamp."""

    outcomes: list[SolidBuyOutcome] = []
    for alert in alerts:
        if not is_buy_alert(alert):
            continue
        entry = _entry_price(alert)
        if entry is None:
            continue
        future = tuple(
            sorted(
                (
                    bar
                    for bar in minute_bars.get(alert.symbol, ())
                    if bar.symbol == alert.symbol
                    and bar.timeframe is BarTimeframe.MINUTE_1
                    and bar.is_final
                    and bar.timestamp > alert.created_at
                ),
                key=lambda bar: bar.timestamp,
            )
        )
        evaluation_window = tuple(
            bar
            for bar in future
            if bar.timestamp <= alert.created_at + timedelta(minutes=60)
        )
        invalidation = _level(alert, "invalidation", "invalidation_level")
        target = _level(alert, "objective", "target_2r", "objective_level", "target_price")
        outcomes.append(
            SolidBuyOutcome(
                alert_id=alert.alert_id,
                symbol=alert.symbol,
                alert_kind=alert.kind.value,
                occurred_at=alert.created_at,
                entry_price=entry,
                invalidation=invalidation,
                target=target,
                first_level_hit=_first_level_hit(
                    evaluation_window,
                    invalidation=invalidation,
                    target=target,
                ),
                mfe_percent=(
                    _percent(max(bar.high for bar in evaluation_window), entry)
                    if evaluation_window
                    else None
                ),
                mae_percent=(
                    _percent(min(bar.low for bar in evaluation_window), entry)
                    if evaluation_window
                    else None
                ),
                return_15m=_return_at(future, alert.created_at, entry, minutes=15),
                return_30m=_return_at(future, alert.created_at, entry, minutes=30),
                return_60m=_return_at(future, alert.created_at, entry, minutes=60),
                return_close=_return_at_close(future, alert.created_at, entry),
                evaluated_through=future[-1].timestamp if future else None,
                engine_versions=tuple(
                    dict.fromkeys(
                        f"{analysis.engine_id}@{analysis.engine_version}"
                        for analysis in alert.component_analyses
                    )
                ),
                entry_confirmation_rule_versions=_rule_versions(alert),
            )
        )
    return tuple(outcomes)


def _entry_price(alert: LocalAlert) -> Decimal | None:
    direct = _decimal(_metrics(alert).get("current_price"))
    if direct is not None:
        return direct
    analyses = {item.horizon: item for item in alert.component_analyses}
    for horizon in (
        AnalysisHorizon.INTRADAY,
        AnalysisHorizon.SWING,
        AnalysisHorizon.LONG_TERM,
    ):
        analysis = analyses.get(horizon)
        if analysis is not None:
            price = _decimal(_metrics(analysis).get("reference_price"))
            if price is not None:
                return price
    return None


def _level(alert: LocalAlert, *names: str) -> Decimal | None:
    values = (_metrics(alert), *(_metrics(item) for item in alert.component_analyses))
    for name in names:
        for metrics in values:
            value = _decimal(metrics.get(name))
            if value is not None:
                return value
    return None


def _return_at(
    bars: tuple[MarketBar, ...],
    occurred_at: datetime,
    entry: Decimal,
    *,
    minutes: int,
) -> Decimal | None:
    target_at = occurred_at + timedelta(minutes=minutes)
    bar = next(
        (
            item
            for item in bars
            if target_at <= item.timestamp <= target_at + timedelta(minutes=1)
        ),
        None,
    )
    return _percent(bar.close, entry) if bar is not None else None


def _return_at_close(
    bars: tuple[MarketBar, ...], occurred_at: datetime, entry: Decimal
) -> Decimal | None:
    market_date = occurred_at.astimezone(_NEW_YORK).date()
    same_session = tuple(
        bar for bar in bars if bar.timestamp.astimezone(_NEW_YORK).date() == market_date
    )
    if not same_session:
        return None
    latest = same_session[-1]
    if latest.timestamp.astimezone(_NEW_YORK).time() < time(15, 59):
        return None
    return _percent(latest.close, entry)


def _first_level_hit(
    bars: tuple[MarketBar, ...],
    *,
    invalidation: Decimal | None,
    target: Decimal | None,
) -> FirstLevelHit | None:
    for bar in bars:
        target_hit = target is not None and bar.high >= target
        invalidation_hit = invalidation is not None and bar.low <= invalidation
        if target_hit and invalidation_hit:
            return "AMBIGUOUS"
        if target_hit:
            return "TARGET"
        if invalidation_hit:
            return "INVALIDATION"
    return None


def _rule_versions(alert: LocalAlert) -> tuple[str, ...]:
    values = (_metrics(alert), *(_metrics(item) for item in alert.component_analyses))
    return tuple(
        dict.fromkeys(
            str(version)
            for metrics in values
            if (version := metrics.get("entry_confirmation_rule_version")) is not None
        )
    )


def _metrics(value: LocalAlert | object) -> dict[str, object]:
    raw_metrics = getattr(value, "metrics", ())
    return {item.name: item.value for item in raw_metrics}


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return Decimal(str(value))
    except (ValueError, ArithmeticError):
        return None


def _percent(price: Decimal, entry: Decimal) -> Decimal:
    return ((price / entry - Decimal("1")) * Decimal("100")).quantize(
        _FOUR_PLACES,
        rounding=ROUND_HALF_UP,
    )
