"""Native-zone-first Support Confirmation enrichment for standalone 4HGERI v1.6."""

from __future__ import annotations

from decimal import Decimal

from app.contracts import NamedValue, SupportAssessment
from app.support_confirmation_engine import classify_support_enrichment

from .v15 import Swing4HGeriEngineV15


class Swing4HGeriEngineV16(Swing4HGeriEngineV15):
    """Use Support only to corroborate matching GERI main or tactical LONG zones."""

    engine_version = "1.6.0"

    def _classify_support(self, support: SupportAssessment, current_price: Decimal) -> str | None:
        return classify_support_enrichment(support, current_price=current_price)

    def _support_metrics(
        self,
        support: SupportAssessment,
        strength: str,
        matched_zone: str,
    ) -> tuple[NamedValue, ...]:
        return (
            *super()._support_metrics(support, strength, matched_zone),
            NamedValue(name="support_zone_position", value=support.zone_position.value),
            NamedValue(name="support_zone_distance_atr", value=support.zone_distance_atr),
            NamedValue(name="support_touch_count", value=support.touch_count),
            NamedValue(name="support_touch_age_sessions", value=support.touch_age_sessions),
            NamedValue(name="support_actionability_score", value=support.actionability_score),
            NamedValue(name="support_b_wave_risk", value=support.b_wave_risk),
            NamedValue(name="support_four_hour_reclaim", value=support.four_hour_reclaim),
            NamedValue(
                name="support_four_hour_higher_high",
                value=support.four_hour_higher_high,
            ),
            NamedValue(
                name="support_four_hour_higher_low",
                value=support.four_hour_higher_low,
            ),
        )
