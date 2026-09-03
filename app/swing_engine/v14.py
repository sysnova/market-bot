"""Failed-breakout SHORT thesis detection over the stable Swing v12 geometry."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import cast
from uuid import UUID

from app.contracts import (
    AnalysisResult,
    AnalysisVerdict,
    NamedValue,
    PatternDirection,
)

from .models import SwingContext
from .v12 import SwingEngineV12

ZERO = Decimal("0")
HUNDRED = Decimal("100")
_BLOCKING_FAILED_BREAKOUT_STATES = {"ACTIVE", "NEW_BREAKOUT_PENDING"}


class SwingEngineV14(SwingEngineV12):
    """Declare a broken LONG thesis when failed-breakout evidence loses structure."""

    engine_version = "14.0.0"
    short_failed_breakout_states = frozenset(_BLOCKING_FAILED_BREAKOUT_STATES)

    def __init__(
        self,
        *,
        short_confirmation_enabled: bool = True,
        short_below_sma20_required: bool = True,
        short_below_sma50_required: bool = True,
        short_below_breakout_avwap_required: bool = True,
        short_minimum_sma50_break_percent: Decimal = Decimal("2"),
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._short_confirmation_enabled = short_confirmation_enabled
        self._short_below_sma20_required = short_below_sma20_required
        self._short_below_sma50_required = short_below_sma50_required
        self._short_below_breakout_avwap_required = (
            short_below_breakout_avwap_required
        )
        if short_minimum_sma50_break_percent < ZERO:
            raise ValueError("short minimum SMA50 break percent cannot be negative")
        self._short_minimum_sma50_break_percent = short_minimum_sma50_break_percent

    def analyze(
        self,
        context: SwingContext,
        *,
        source_event_ids: tuple[UUID, ...] = (),
    ) -> AnalysisResult:
        result = super().analyze(context, source_event_ids=source_event_ids)
        metrics = _metrics(result)
        thesis_broken = self._short_thesis_broken(context.price, metrics)
        setup_id = _short_setup_id(context.symbol, metrics) if thesis_broken else None
        additions = (
            NamedValue(name="short_thesis_broken", value=thesis_broken),
            NamedValue(name="short_structure_gate_passed", value=thesis_broken),
            NamedValue(name="short_setup_id", value=setup_id),
            NamedValue(
                name="short_thesis_rule_version",
                value=self._strategy_version,
            ),
        )
        if not thesis_broken:
            return result.model_copy(
                update={
                    "engine_version": self.engine_version,
                    "metrics": _upsert_metrics(result, *additions),
                }
            )

        score = min(result.score, Decimal("35.00"))
        risk_flags = tuple(
            dict.fromkeys((*_strings(metrics.get("risk_flags")), "short_thesis_broken"))
        )
        return result.model_copy(
            update={
                "engine_version": self.engine_version,
                "verdict": AnalysisVerdict.AVOID,
                "direction": PatternDirection.BEARISH,
                "score": score,
                "confidence": (score / HUNDRED).quantize(Decimal("0.0001")),
                "reasons": tuple(
                    dict.fromkeys(
                        (*result.reasons, "failed_breakout_short_thesis_broken")
                    )
                ),
                "metrics": _upsert_metrics(
                    result,
                    NamedValue(name="classification", value="avoid"),
                    NamedValue(name="risk_flags", value=risk_flags),
                    *additions,
                ),
            }
        )

    def _short_thesis_broken(
        self,
        price: Decimal,
        metrics: dict[str, object],
    ) -> bool:
        if not self._short_confirmation_enabled:
            return False
        failed_breakout = str(metrics.get("failed_breakout_state", "NONE")) in (
            self.short_failed_breakout_states
        )
        sma20 = _decimal(metrics.get("daily_sma20"))
        sma50 = _decimal(metrics.get("daily_sma50"))
        breakout_avwap_delta = _decimal(
            metrics.get("price_vs_breakout_avwap_percent")
        )
        below_sma20 = sma20 is not None and price < sma20
        below_sma50 = (
            sma50 is not None
            and price
            <= sma50
            * (Decimal("1") - self._short_minimum_sma50_break_percent / HUNDRED)
        )
        below_breakout_avwap = (
            breakout_avwap_delta is not None and breakout_avwap_delta < ZERO
        )
        return (
            failed_breakout
            and (below_sma20 or not self._short_below_sma20_required)
            and (below_sma50 or not self._short_below_sma50_required)
            and (
                below_breakout_avwap
                or not self._short_below_breakout_avwap_required
            )
        )


def _short_setup_id(symbol: str, metrics: dict[str, object]) -> str:
    failed_at = metrics.get("failed_breakout_at")
    if isinstance(failed_at, datetime):
        anchor = failed_at.isoformat()
    elif isinstance(failed_at, str) and failed_at:
        anchor = failed_at
    else:
        anchor = "unknown-anchor"
    return f"swing-short:{symbol}:{anchor}"


def _metrics(result: AnalysisResult) -> dict[str, object]:
    return {item.name: item.value for item in result.metrics}


def _decimal(value: object) -> Decimal | None:
    return value if isinstance(value, Decimal) else None


def _strings(value: object) -> tuple[str, ...]:
    if isinstance(value, list):
        items: list[object] | tuple[object, ...] = cast(list[object], value)
    elif isinstance(value, tuple):
        items = cast(tuple[object, ...], value)
    else:
        return ()
    return tuple(item for item in items if isinstance(item, str))


def _upsert_metrics(result: AnalysisResult, *items: NamedValue) -> tuple[NamedValue, ...]:
    names = {item.name for item in items}
    return (*(item for item in result.metrics if item.name not in names), *items)
