"""As-of-aware daily resistance and safe partial-history handling for Swing v7."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from app.contracts import AnalysisResult, MarketBar, NamedValue

from .engine import SwingEngine
from .models import SwingContext
from .v6 import SwingEngineV6

_NEW_YORK = ZoneInfo("America/New_York")


class SwingEngineV7(SwingEngineV6):
    """Use every completed prior-session bar and fail closed on short history."""

    engine_version = "7.0.0"

    def analyze(
        self,
        context: SwingContext,
        *,
        source_event_ids: tuple[UUID, ...] = (),
    ) -> AnalysisResult:
        if len(context.daily_bars) < 50 or len(context.intraday_bars) < 21:
            return SwingEngine.analyze(
                self,
                context,
                source_event_ids=source_event_ids,
            )
        result = super().analyze(context, source_event_ids=source_event_ids)
        included = self._latest_completed_bar_is_prior_session(context)
        return result.model_copy(
            update={
                "metrics": _upsert(
                    result,
                    NamedValue(
                        name="resistance_latest_completed_bar_included",
                        value=included,
                    ),
                )
            }
        )

    def _resistance_bars(self, context: SwingContext) -> tuple[MarketBar, ...]:
        if self._latest_completed_bar_is_prior_session(context):
            return context.daily_bars[-self._resistance_lookback_days :]
        return super()._resistance_bars(context)

    @staticmethod
    def _latest_completed_bar_is_prior_session(context: SwingContext) -> bool:
        latest = context.daily_bars[-1]
        return _market_date(latest.timestamp) < _market_date(context.as_of)


def _market_date(value: datetime) -> date:
    return value.astimezone(_NEW_YORK).date()


def _upsert(result: AnalysisResult, *items: NamedValue) -> tuple[NamedValue, ...]:
    names = {item.name for item in items}
    return (*(item for item in result.metrics if item.name not in names), *items)
