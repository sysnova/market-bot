"""Local-trigger SHORT efficiency with EMA20 extension retained as a warning."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from app.contracts import AnalysisResult, NamedValue

from .models import IntradayContext
from .v5 import IntradayEngineV5

_BEARISH_SETUPS = {"bearish_breakdown", "bearish_vwap_rejection"}


class IntradayEngineV6(IntradayEngineV5):
    """Confirm fresh local breakdowns without making EMA20 distance a hard veto."""

    engine_version = "6.0.0"

    def __init__(
        self,
        *,
        short_ema20_extension_hard_gate: bool = False,
        **kwargs: object,
    ) -> None:
        super().__init__(
            short_ema20_extension_required=short_ema20_extension_hard_gate,
            **kwargs,  # pyright: ignore[reportArgumentType]
        )
        self._short_ema20_extension_hard_gate = short_ema20_extension_hard_gate

    def analyze(
        self,
        context: IntradayContext,
        *,
        source_event_ids: tuple[UUID, ...] = (),
    ) -> AnalysisResult:
        result = super().analyze(context, source_event_ids=source_event_ids)
        metrics = _metrics(result)
        setup = str(metrics.get("setup", "no_trigger"))
        extension = metrics.get("short_ema20_extension_atr")
        extended = (
            setup in _BEARISH_SETUPS
            and isinstance(extension, Decimal)
            and extension > self._maximum_ema20_extension_atr
        )
        reasons = result.reasons
        if extended:
            reasons = tuple(dict.fromkeys((*reasons, "short_extended_below_ema20")))
        return result.model_copy(
            update={
                "engine_version": self.engine_version,
                "reasons": reasons,
                "metrics": _upsert_metrics(
                    result,
                    NamedValue(name="short_ema20_extension_warning", value=extended),
                    NamedValue(
                        name="short_ema20_extension_hard_gate",
                        value=self._short_ema20_extension_hard_gate,
                    ),
                ),
            }
        )


def _metrics(result: AnalysisResult) -> dict[str, object]:
    return {item.name: item.value for item in result.metrics}


def _upsert_metrics(result: AnalysisResult, *items: NamedValue) -> tuple[NamedValue, ...]:
    names = {item.name for item in items}
    return (*(item for item in result.metrics if item.name not in names), *items)
