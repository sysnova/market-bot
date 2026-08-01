"""Immutable policy and evaluation values owned by PatreonCaps."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contracts import (
    AnalysisResult,
    MacroRegime,
    MarketBar,
    NamedValue,
    PatreonCapsAssessment,
    PatreonCapsState,
    PatreonCapsTransition,
)


class FrozenModel(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")


class PatreonCapsPolicy(FrozenModel):
    rule_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    portfolio_capital_usd: Decimal = Field(gt=Decimal())
    minimum_confluence_score: Decimal = Field(ge=Decimal(), le=Decimal("100"))
    minimum_source_families: int = Field(ge=1)
    minimum_confirmation_score: Decimal = Field(ge=Decimal(), le=Decimal("100"))
    cluster_distance_atr: Decimal = Field(gt=Decimal())
    cluster_width_atr: Decimal = Field(gt=Decimal())
    zone_padding_atr: Decimal = Field(ge=Decimal())
    test_padding_atr: Decimal = Field(ge=Decimal())
    invalidation_buffer_atr: Decimal = Field(gt=Decimal())
    defense_distance_atr: Decimal = Field(gt=Decimal())
    v_rvol_minimum: Decimal = Field(gt=Decimal())
    base_rvol_minimum: Decimal = Field(gt=Decimal())
    impulse_minimum_atr: Decimal = Field(gt=Decimal())
    watch_ttl: timedelta = Field(gt=timedelta())
    long_max_age: timedelta = Field(gt=timedelta())
    swing_max_age: timedelta = Field(gt=timedelta())
    intraday_max_age: timedelta = Field(gt=timedelta())
    macro_thresholds: dict[MacroRegime, Decimal]
    macro_symbols: tuple[str, ...]
    lesson_enabled: bool = False
    require_daily_above_sma200: bool = False
    cross_lookback_bars: int = Field(default=20, ge=1)
    triangle_lookback_bars: int = Field(default=80, ge=20)
    triangle_tolerance_atr: Decimal = Field(default=Decimal("0.35"), gt=Decimal())
    wave_0618_tolerance_atr: Decimal = Field(default=Decimal("0.15"), gt=Decimal())
    confluence_weight: Decimal = Field(default=Decimal("0.40"), ge=Decimal(), le=Decimal("1"))
    confirmation_weight: Decimal = Field(default=Decimal("0.30"), ge=Decimal(), le=Decimal("1"))
    alignment_weight: Decimal = Field(default=Decimal("0.30"), ge=Decimal(), le=Decimal("1"))
    lesson_weight: Decimal = Field(default=Decimal("0"), ge=Decimal(), le=Decimal("1"))

    @model_validator(mode="after")
    def validate_score_weights(self) -> PatreonCapsPolicy:
        total = (
            self.confluence_weight
            + self.confirmation_weight
            + self.alignment_weight
            + self.lesson_weight
        )
        if total != Decimal("1"):
            raise ValueError("PatreonCaps score weights must sum to 1")
        return self


class PatreonCapsContext(FrozenModel):
    symbol: str
    as_of: datetime
    daily_bars: tuple[MarketBar, ...]
    weekly_bars: tuple[MarketBar, ...]
    hourly_bars: tuple[MarketBar, ...] = ()
    intraday_bars: tuple[MarketBar, ...]
    analyses: tuple[AnalysisResult, ...]
    macro_regime: MacroRegime
    macro_signals: tuple[str, ...] = ()
    macro_metrics: tuple[NamedValue, ...] = ()
    portfolio_capital_usd: Decimal
    target_weight_percent: Decimal | None = None
    held_quantity: Decimal = Decimal()

    @model_validator(mode="after")
    def validate_context(self) -> PatreonCapsContext:
        symbol = self.symbol.strip().upper()
        if not symbol:
            raise ValueError("symbol is required")
        for bars in (
            self.daily_bars,
            self.weekly_bars,
            self.hourly_bars,
            self.intraday_bars,
        ):
            if any(bar.symbol != symbol or not bar.is_final for bar in bars):
                raise ValueError("context bars must be final and belong to symbol")
        if any(item.symbol != symbol for item in self.analyses):
            raise ValueError("context analyses must belong to symbol")
        return self


class SupportLevel(FrozenModel):
    name: str
    family: str
    value: Decimal = Field(gt=Decimal())
    center_weight: Decimal = Field(gt=Decimal())
    score_points: Decimal = Field(ge=Decimal())


class SupportZone(FrozenModel):
    low: Decimal
    center: Decimal
    high: Decimal
    invalidation: Decimal
    atr14: Decimal
    score: Decimal
    sources: tuple[str, ...]
    defense_dates: tuple[str, ...]


class TrancheSizing(FrozenModel):
    stage: int = Field(ge=1, le=5)
    risk_budget_usd: Decimal
    target_capital_usd: Decimal
    remaining_target_usd: Decimal
    suggested_tranche_usd: Decimal
    suggested_whole_shares: Decimal


class PatreonCapsWatch(FrozenModel):
    watch_id: UUID
    symbol: str
    rule_version: str
    state: PatreonCapsState
    armed_at: datetime
    updated_at: datetime
    expires_at: datetime
    zone_low: Decimal
    zone_center: Decimal
    zone_high: Decimal
    invalidation: Decimal
    highest_price: Decimal
    tranche_stage: int = Field(ge=0, le=5)
    saw_macro_shock: bool = False
    support_sources: tuple[str, ...]
    source_analysis_ids: tuple[UUID, ...]


class PatreonCapsEvaluation(FrozenModel):
    assessment: PatreonCapsAssessment
    watch: PatreonCapsWatch
    transition: PatreonCapsTransition | None = None
    sizing: TrancheSizing | None = None


class ReplayOutcome(FrozenModel):
    transition_id: UUID
    watch_id: UUID
    symbol: str
    occurred_at: datetime
    entry_price: Decimal
    return_5d: Decimal | None = None
    return_20d: Decimal | None = None
    return_60d: Decimal | None = None
    mfe_percent: Decimal | None = None
    mae_percent: Decimal | None = None
    invalidated: bool
    confirmation_hours: Decimal | None = None


class LessonAssessment(FrozenModel):
    enabled: bool
    score: Decimal = Field(ge=Decimal(), le=Decimal("100"))
    gate_passed: bool
    golden_cross: bool = False
    death_cross: bool = False
    ascending_triangle: bool = False
    triangle_breakout: bool = False
    triangle_retest: bool = False
    wave2_0618_hold: bool = False
    wave1_high_retest: bool = False
    reasons: tuple[str, ...]
    metrics: tuple[NamedValue, ...] = ()
