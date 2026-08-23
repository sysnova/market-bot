"""Versioned thresholds for the first scalping decision policy."""

from decimal import Decimal

from pydantic import Field

from app.contracts._base import StrictFrozenModel, UnitInterval


class ScalpPolicy(StrictFrozenModel):
    """Explicit microstructure gates; all durations are event-time based."""

    max_spread_bps: Decimal = Field(default=Decimal("15"), gt=Decimal("0"))
    max_order_flow_age_ms: int = Field(default=3_000, gt=0)
    max_quote_age_ms: Decimal = Field(default=Decimal("1500"), gt=Decimal("0"))
    minimum_order_flow_confidence: UnitInterval = Decimal("0.65")
    minimum_data_quality: UnitInterval = Decimal("0.70")
    maximum_unknown_trade_ratio: UnitInterval = Decimal("0.35")
    reversal_confidence: UnitInterval = Decimal("0.72")
    support_tolerance_atr: Decimal = Field(default=Decimal("0.20"), ge=Decimal("0"))
    invalidation_atr: Decimal = Field(default=Decimal("0.25"), gt=Decimal("0"))
    reward_risk_ratio: Decimal = Field(default=Decimal("1.50"), gt=Decimal("0"))
    max_hold_seconds: int = Field(default=900, gt=0)
    rearm_cooldown_seconds: int = Field(default=60, ge=0)
