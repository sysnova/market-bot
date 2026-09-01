"""Abrupt-displacement SHORT confirmation preserving Intraday v6 for rollback."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from app.contracts import AnalysisResult, AnalysisVerdict, NamedValue

from .models import IntradayContext
from .v6 import IntradayEngineV6

ZERO = Decimal("0")
HUNDRED = Decimal("100")
_BEARISH_SETUPS = {"bearish_breakdown", "bearish_vwap_rejection"}


class IntradayEngineV7(IntradayEngineV6):
    """Confirm an extended SHORT only when price is undergoing strong displacement."""

    engine_version = "7.0.0"

    def __init__(
        self,
        *,
        short_displacement_enabled: bool = True,
        short_displacement_minimum_momentum_percent: Decimal = Decimal("0.50"),
        short_displacement_minimum_rvol: Decimal = Decimal("2.00"),
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)  # pyright: ignore[reportArgumentType]
        self._short_displacement_enabled = short_displacement_enabled
        self._short_displacement_minimum_momentum_percent = (
            short_displacement_minimum_momentum_percent
        )
        self._short_displacement_minimum_rvol = short_displacement_minimum_rvol

    def analyze(
        self,
        context: IntradayContext,
        *,
        source_event_ids: tuple[UUID, ...] = (),
    ) -> AnalysisResult:
        result = super().analyze(context, source_event_ids=source_event_ids)
        metrics = _metrics(result)
        setup = str(metrics.get("setup", "no_trigger"))
        momentum = metrics.get("momentum_5_percent")
        relative_volume = metrics.get("relative_volume")
        extended = metrics.get("short_entry_efficiency_gate_passed") is False
        displacement_gate = (
            self._short_displacement_enabled
            and setup in _BEARISH_SETUPS
            and extended
            and metrics.get("short_confirmation_gate_passed") is True
            and metrics.get("short_mature_retest_confirmed") is True
            and metrics.get("risk_ok") is True
            and isinstance(momentum, Decimal)
            and momentum <= -self._short_displacement_minimum_momentum_percent
            and isinstance(relative_volume, Decimal)
            and relative_volume >= self._short_displacement_minimum_rvol
        )

        reasons = list(result.reasons)
        if displacement_gate:
            reasons = [reason for reason in reasons if reason != "short_late_entry_wait_retest"]
            reasons.extend(("short_displacement_confirmed", "short_extension_override_applied"))
        score = max(result.score, Decimal("70")) if displacement_gate else result.score
        timing = (
            "confirmed_displacement"
            if displacement_gate
            else str(metrics.get("short_entry_timing", "not_applicable"))
        )
        return result.model_copy(
            update={
                "engine_version": self.engine_version,
                "verdict": AnalysisVerdict.FAVORABLE if displacement_gate else result.verdict,
                "score": _score(score),
                "confidence": (_score(score) / HUNDRED).quantize(Decimal("0.0001")),
                "reasons": tuple(dict.fromkeys(reasons)),
                "metrics": _upsert_metrics(
                    result,
                    NamedValue(name="short_displacement_gate_passed", value=displacement_gate),
                    NamedValue(
                        name="short_extension_override_applied",
                        value=displacement_gate,
                    ),
                    NamedValue(
                        name="short_mature_confirmation_gate_passed",
                        value=(
                            displacement_gate
                            or metrics.get("short_mature_confirmation_gate_passed") is True
                        ),
                    ),
                    NamedValue(name="short_entry_timing", value=timing),
                    NamedValue(
                        name="short_entry_lane",
                        value="DISPLACEMENT" if displacement_gate else "STANDARD",
                    ),
                    NamedValue(
                        name="short_displacement_minimum_momentum_percent",
                        value=self._short_displacement_minimum_momentum_percent,
                    ),
                    NamedValue(
                        name="short_displacement_minimum_rvol",
                        value=self._short_displacement_minimum_rvol,
                    ),
                    NamedValue(
                        name="confirmation_gate_passed",
                        value=(
                            displacement_gate
                            or metrics.get("confirmation_gate_passed") is True
                        ),
                    ),
                    NamedValue(
                        name="mature_confirmation_gate_passed",
                        value=(
                            displacement_gate
                            or metrics.get("mature_confirmation_gate_passed") is True
                        ),
                    ),
                    NamedValue(name="entry_timing", value=timing),
                ),
            }
        )


def _metrics(result: AnalysisResult) -> dict[str, object]:
    return {item.name: item.value for item in result.metrics}


def _upsert_metrics(result: AnalysisResult, *items: NamedValue) -> tuple[NamedValue, ...]:
    names = {item.name for item in items}
    return (*(item for item in result.metrics if item.name not in names), *items)


def _score(value: Decimal) -> Decimal:
    return min(HUNDRED, max(ZERO, value)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
