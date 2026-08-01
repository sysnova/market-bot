"""Stateful, deterministic PatreonCaps shadow engine."""

from __future__ import annotations

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from app.contracts import (
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    MacroRegime,
    MarketBar,
    PatreonCapsAssessment,
    PatreonCapsState,
    PatreonCapsTransition,
    PatternDirection,
    StrategyMode,
    new_uuid7,
)

from .indicators import (
    anchored_vwap,
    atr,
    confirmed_impulse_indices,
    confirmed_pivot_indices,
    fibonacci_levels,
    last_breakout_index,
    relative_volume,
    rounded,
    rsi,
    sma,
)
from .lesson import evaluate_lesson
from .models import (
    LessonAssessment,
    PatreonCapsContext,
    PatreonCapsEvaluation,
    PatreonCapsPolicy,
    PatreonCapsWatch,
    SupportLevel,
    SupportZone,
    TrancheSizing,
)
from .sizing import size_portfolio_tranche

ZERO = Decimal()
HUNDRED = Decimal("100")
_SOURCE_DEFINITION: dict[str, tuple[str, Decimal, Decimal]] = {
    "pivot_weekly": ("pivot_weekly", Decimal("5"), Decimal("16")),
    "pivot_daily": ("pivot_daily", Decimal("4"), Decimal("14")),
    "avwap": ("avwap", Decimal("4"), Decimal("12")),
    "breakout": ("breakout", Decimal("4"), Decimal("12")),
    "sma_weekly": ("sma_weekly", Decimal("3"), Decimal("10")),
    "sma_daily": ("sma_daily", Decimal("2"), Decimal("8")),
    "fibonacci": ("fibonacci", Decimal("2"), Decimal("5")),
    "round": ("round", Decimal("1"), Decimal("3")),
}


class PatreonCapsEngine:
    """Freeze support theses and emit independently measurable shadow transitions."""

    engine_id = "patreon-caps"

    def __init__(
        self,
        policy: PatreonCapsPolicy,
        *,
        restored_watches: tuple[PatreonCapsWatch, ...] = (),
    ) -> None:
        self._policy = policy
        self._watches = {item.symbol: item for item in restored_watches}

    def evaluate(
        self, context: PatreonCapsContext, *, now: datetime
    ) -> PatreonCapsEvaluation | None:
        if (
            len(context.daily_bars) < 260
            or len(context.weekly_bars) < 220
            or len(context.intraday_bars) < 160
            or (self._policy.lesson_enabled and len(context.hourly_bars) < 205)
        ):
            return None
        current_price = _current_price(context)
        daily_atr = atr(context.daily_bars, 14)
        analyses = _fresh_analyses(context, self._policy, now)
        alignment_score, alignment_reasons, alignment_blocked = _alignment(analyses)
        existing = self._watches.get(context.symbol)
        zone = (
            _frozen_zone(existing, daily_atr, context.daily_bars)
            if existing is not None
            else _best_support_zone(context, daily_atr, self._policy)
        )
        if zone is None:
            return None
        confirmation_score, confirmation_reasons = _confirmation_score(
            context, zone
        )
        lesson = evaluate_lesson(
            context.daily_bars,
            context.hourly_bars,
            atr14=daily_atr,
            policy=self._policy,
        )
        patreon_score = _score(
            zone.score * self._policy.confluence_weight
            + confirmation_score * self._policy.confirmation_weight
            + alignment_score * self._policy.alignment_weight
            + lesson.score * self._policy.lesson_weight
        )
        macro_threshold = self._policy.macro_thresholds.get(context.macro_regime)
        source_ids = tuple(item.analysis_id for item in analyses.values())
        reasons = (*alignment_reasons, *confirmation_reasons, *lesson.reasons)

        if existing is None:
            if not _can_arm(
                context,
                zone,
                current_price,
                analyses,
                self._policy,
            ):
                return None
            watch = PatreonCapsWatch(
                watch_id=new_uuid7(),
                symbol=context.symbol,
                rule_version=self._policy.rule_version,
                state=PatreonCapsState.WATCH_ZONE,
                armed_at=now,
                updated_at=now,
                expires_at=now + self._policy.watch_ttl,
                zone_low=zone.low,
                zone_center=zone.center,
                zone_high=zone.high,
                invalidation=zone.invalidation,
                highest_price=current_price,
                tranche_stage=0,
                saw_macro_shock=context.macro_regime is MacroRegime.SHOCK,
                support_sources=zone.sources,
                source_analysis_ids=source_ids,
            )
            self._watches[context.symbol] = watch
            assessment = _assessment(
                context,
                watch,
                zone,
                confirmation_score,
                alignment_score,
                lesson,
                patreon_score,
                macro_threshold,
                ("watch_zone_armed", *reasons),
            )
            return PatreonCapsEvaluation(
                assessment=assessment,
                watch=watch,
                transition=_transition(
                    assessment,
                    watch,
                    previous=None,
                    confirmation_type=None,
                    sizing=None,
                ),
            )

        next_state, transition_reason, confirmation_type, next_stage = self._next_state(
            context=context,
            watch=existing,
            zone=zone,
            current_price=current_price,
            confirmation_score=confirmation_score,
            alignment_score=alignment_score,
            alignment_blocked=alignment_blocked,
            lesson_gate_passed=lesson.gate_passed,
            patreon_score=patreon_score,
            macro_threshold=macro_threshold,
            analyses=analyses,
            now=now,
        )
        watch = existing.model_copy(
            update={
                "state": next_state,
                "updated_at": now,
                "highest_price": max(existing.highest_price, current_price),
                "tranche_stage": next_stage,
                "saw_macro_shock": (
                    existing.saw_macro_shock or context.macro_regime is MacroRegime.SHOCK
                ),
                "source_analysis_ids": source_ids or existing.source_analysis_ids,
            }
        )
        self._watches[context.symbol] = watch
        assessment = _assessment(
            context,
            watch,
            zone,
            confirmation_score,
            alignment_score,
            lesson,
            patreon_score,
            macro_threshold,
            ((transition_reason,) if transition_reason else reasons),
        )
        if next_state is existing.state and next_stage == existing.tranche_stage:
            return PatreonCapsEvaluation(assessment=assessment, watch=watch)
        sizing = None
        if next_stage > existing.tranche_stage:
            sizing = size_portfolio_tranche(
                portfolio_capital_usd=context.portfolio_capital_usd,
                target_weight_percent=context.target_weight_percent,
                held_quantity=context.held_quantity,
                entry_price=current_price,
                invalidation=watch.invalidation,
                stage=next_stage,
                macro_regime=context.macro_regime,
            )
        return PatreonCapsEvaluation(
            assessment=assessment,
            watch=watch,
            sizing=sizing,
            transition=_transition(
                assessment,
                watch,
                previous=existing.state,
                confirmation_type=confirmation_type,
                sizing=sizing,
            ),
        )

    def _next_state(
        self,
        *,
        context: PatreonCapsContext,
        watch: PatreonCapsWatch,
        zone: SupportZone,
        current_price: Decimal,
        confirmation_score: Decimal,
        alignment_score: Decimal,
        alignment_blocked: bool,
        lesson_gate_passed: bool,
        patreon_score: Decimal,
        macro_threshold: Decimal | None,
        analyses: dict[AnalysisHorizon, AnalysisResult],
        now: datetime,
    ) -> tuple[PatreonCapsState, str, str | None, int]:
        long = analyses.get(AnalysisHorizon.LONG_TERM)
        if (
            context.daily_bars[-1].close < watch.invalidation
            or _long_blocks(long)
        ):
            return (
                PatreonCapsState.INVALIDATED,
                "structural_invalidation",
                None,
                watch.tranche_stage,
            )
        if now >= watch.expires_at:
            return PatreonCapsState.EXPIRED, "watch_expired", None, watch.tranche_stage
        in_test = (
            watch.zone_low - zone.atr14 * self._policy.test_padding_atr
            <= current_price
            <= watch.zone_high + zone.atr14 * self._policy.test_padding_atr
        )
        if watch.state is PatreonCapsState.WATCH_ZONE and in_test:
            return PatreonCapsState.SUPPORT_TEST, "support_test", None, watch.tranche_stage

        buy_allowed = (
            not alignment_blocked
            and lesson_gate_passed
            and macro_threshold is not None
            and patreon_score >= macro_threshold
            and confirmation_score >= self._policy.minimum_confirmation_score
        )
        intraday = analyses.get(AnalysisHorizon.INTRADAY)
        swing = analyses.get(AnalysisHorizon.SWING)
        intraday_metrics = _metrics(intraday)
        swing_metrics = _metrics(swing)
        rvol = relative_volume(context.intraday_bars, 20)
        reclaimed = current_price >= min(watch.zone_high, anchored_vwap(context.intraday_bars, 0))
        if watch.state is PatreonCapsState.SUPPORT_TEST and buy_allowed:
            base_confirmed = (
                len(zone.defense_dates) >= 2
                and _base_structure_confirmed(context.daily_bars, watch, zone)
                and rvol >= self._policy.base_rvol_minimum
                and swing is not None
                and swing.verdict is AnalysisVerdict.FAVORABLE
                and swing_metrics.get("anchored_vwap_gate_passed") is True
                and intraday_metrics.get("confirmation_gate_passed") is True
            )
            if base_confirmed:
                return PatreonCapsState.CONFIRMED_BASE, "confirmed_base", "BASE", 1
            v_confirmed = (
                _touched_zone_current_session(context.intraday_bars, watch, zone)
                and
                reclaimed
                and rvol >= self._policy.v_rvol_minimum
                and intraday_metrics.get("confirmation_gate_passed") is True
            )
            if v_confirmed:
                return PatreonCapsState.CONFIRMED_V, "confirmed_v", "V", 1

        confirmed_states = {
            PatreonCapsState.CONFIRMED_V,
            PatreonCapsState.CONFIRMED_BASE,
            PatreonCapsState.IMPULSE_RETEST,
        }
        if watch.state in confirmed_states and buy_allowed and _continuation_aligned(analyses):
            if watch.tranche_stage == 1:
                advanced = watch.highest_price >= watch.zone_high + zone.atr14
                retested = in_test and reclaimed
                if advanced and retested:
                    return PatreonCapsState.IMPULSE_RETEST, "impulse_retest", "RETEST", 2
            elif watch.tranche_stage == 2 and current_price >= watch.highest_price + zone.atr14:
                return PatreonCapsState.IMPULSE_RETEST, "continuation_breakout", "BREAKOUT", 3
            elif (
                watch.tranche_stage == 3
                and _has_higher_low(context.intraday_bars)
                and current_price >= anchored_vwap(context.intraday_bars, 0)
            ):
                return PatreonCapsState.IMPULSE_RETEST, "continuation_higher_low", "HIGHER_LOW", 4
            elif (
                watch.tranche_stage == 4
                and watch.saw_macro_shock
                and context.macro_regime in {MacroRegime.RISK_ON, MacroRegime.NEUTRAL}
                and reclaimed
            ):
                return PatreonCapsState.IMPULSE_RETEST, "post_shock_reclaim", "RECLAIM", 5
        return watch.state, "", None, watch.tranche_stage


def _current_price(context: PatreonCapsContext) -> Decimal:
    if context.intraday_bars:
        return context.intraday_bars[-1].close
    return context.daily_bars[-1].close


def _fresh_analyses(
    context: PatreonCapsContext, policy: PatreonCapsPolicy, now: datetime
) -> dict[AnalysisHorizon, AnalysisResult]:
    ages = {
        AnalysisHorizon.LONG_TERM: policy.long_max_age,
        AnalysisHorizon.SWING: policy.swing_max_age,
        AnalysisHorizon.INTRADAY: policy.intraday_max_age,
    }
    result: dict[AnalysisHorizon, AnalysisResult] = {}
    for item in context.analyses:
        maximum = ages.get(item.horizon)
        if maximum is None or item.as_of > now or now - item.as_of > maximum:
            continue
        current = result.get(item.horizon)
        if current is None or item.as_of > current.as_of:
            result[item.horizon] = item
    return result


def _alignment(
    analyses: dict[AnalysisHorizon, AnalysisResult],
) -> tuple[Decimal, tuple[str, ...], bool]:
    score = ZERO
    reasons: list[str] = []
    blocked = False
    long = analyses.get(AnalysisHorizon.LONG_TERM)
    if _long_blocks(long):
        blocked = True
        reasons.append("long_blocks_entry")
    elif long is not None and long.direction is PatternDirection.BULLISH:
        if long.verdict is AnalysisVerdict.FAVORABLE:
            score += Decimal("40")
        elif long.verdict is AnalysisVerdict.WATCH:
            score += Decimal("28")
    swing = analyses.get(AnalysisHorizon.SWING)
    swing_metrics = _metrics(swing)
    if swing_metrics.get("structure_broken_confirmed") is True:
        blocked = True
        reasons.append("swing_structure_broken")
    elif swing is not None and swing.verdict is AnalysisVerdict.FAVORABLE and (
        swing_metrics.get("anchored_vwap_gate_passed") is True
    ):
        score += Decimal("35")
    elif swing is not None and swing.verdict is AnalysisVerdict.WATCH:
        score += Decimal("20")
    intraday = analyses.get(AnalysisHorizon.INTRADAY)
    intraday_metrics = _metrics(intraday)
    if intraday is not None and intraday.verdict is AnalysisVerdict.FAVORABLE and (
        intraday_metrics.get("confirmation_gate_passed") is True
    ):
        score += Decimal("25")
    elif str(intraday_metrics.get("setup", "")) in {
        "bullish_breakout",
        "bullish_vwap_reclaim",
        "bullish_entry_confirmation",
    }:
        score += Decimal("12")
    return _score(score), tuple(reasons) or ("multi_horizon_alignment_evaluated",), blocked


def _long_blocks(result: AnalysisResult | None) -> bool:
    return result is not None and (
        result.direction is PatternDirection.BEARISH
        or result.verdict is AnalysisVerdict.AVOID
        or _metrics(result).get("structure_broken_confirmed") is True
    )


def _metrics(result: AnalysisResult | None) -> dict[str, object]:
    return {} if result is None else {item.name: item.value for item in result.metrics}


def _best_support_zone(
    context: PatreonCapsContext,
    daily_atr: Decimal,
    policy: PatreonCapsPolicy,
) -> SupportZone | None:
    levels = _support_levels(context, daily_atr)
    if not levels:
        return None
    price = _current_price(context)
    candidates: list[SupportZone] = []
    for seed in levels:
        clustered = tuple(
            item
            for item in levels
            if abs(item.value - seed.value) / daily_atr <= policy.cluster_distance_atr
        )
        if not clustered or max(item.value for item in clustered) - min(
            item.value for item in clustered
        ) > daily_atr * policy.cluster_width_atr:
            continue
        zone = _zone_from_levels(clustered, context.daily_bars, daily_atr, policy)
        if zone.invalidation < price <= zone.high + daily_atr:
            candidates.append(zone)
    if not candidates:
        return None
    return max(candidates, key=lambda item: (item.score, -abs(price - item.center)))


def _support_levels(
    context: PatreonCapsContext, daily_atr: Decimal
) -> tuple[SupportLevel, ...]:
    levels: list[SupportLevel] = []

    def add(name: str, family: str, value: Decimal | None) -> None:
        if value is None or value <= ZERO:
            return
        _, weight, points = _SOURCE_DEFINITION[family]
        levels.append(SupportLevel(
            name=name,
            family=family,
            value=rounded(value),
            center_weight=weight,
            score_points=points,
        ))

    daily_lows, _ = confirmed_pivot_indices(context.daily_bars)
    weekly_lows, _ = confirmed_pivot_indices(context.weekly_bars)
    if daily_lows:
        add("pivot_daily", "pivot_daily", context.daily_bars[daily_lows[-1]].low)
        add(
            "pivot_daily_avwap",
            "avwap",
            anchored_vwap(context.daily_bars, daily_lows[-1]),
        )
    if weekly_lows:
        add("pivot_weekly", "pivot_weekly", context.weekly_bars[weekly_lows[-1]].low)
    daily_closes = tuple(bar.close for bar in context.daily_bars)
    weekly_closes = tuple(bar.close for bar in context.weekly_bars)
    for period in (20, 50, 150, 200):
        add(f"daily_sma{period}", "sma_daily", sma(daily_closes, period))
    for period in (10, 30, 50, 200):
        add(f"weekly_sma{period}", "sma_weekly", sma(weekly_closes, period))
    swing = next(
        (item for item in context.analyses if item.horizon is AnalysisHorizon.SWING),
        None,
    )
    swing_metrics = _metrics(swing)
    for name in ("pivot_low_avwap", "breakout_avwap"):
        value = swing_metrics.get(name)
        add(name, "avwap", value if isinstance(value, Decimal) else None)
    breakout_index = last_breakout_index(context.daily_bars)
    if breakout_index is not None:
        add("breakout_retest", "breakout", context.daily_bars[breakout_index].close)
        add(
            "breakout_daily_avwap",
            "avwap",
            anchored_vwap(context.daily_bars, breakout_index),
        )
    impulse = confirmed_impulse_indices(context.daily_bars, atr14=daily_atr)
    if impulse is not None:
        add("impulse_daily_avwap", "avwap", anchored_vwap(context.daily_bars, impulse[0]))
    fibs = fibonacci_levels(context.daily_bars, atr14=daily_atr) or {}
    for name, value in fibs.items():
        add(name, "fibonacci", value)
    price = _current_price(context)
    increment = (
        Decimal("0.5")
        if price < Decimal("10")
        else Decimal("1")
        if price < Decimal("50")
        else Decimal("5")
        if price <= Decimal("200")
        else Decimal("10")
    )
    round_level = (price / increment).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * increment
    if abs(round_level - price) <= daily_atr * Decimal("0.5"):
        add("round_number", "round", round_level)
    return tuple(levels)


def _zone_from_levels(
    levels: tuple[SupportLevel, ...],
    daily_bars: tuple[MarketBar, ...],
    daily_atr: Decimal,
    policy: PatreonCapsPolicy,
) -> SupportZone:
    total_weight = sum((item.center_weight for item in levels), ZERO)
    center = rounded(
        sum((item.value * item.center_weight for item in levels), ZERO) / total_weight
    )
    low = rounded(min(item.value for item in levels) - daily_atr * policy.zone_padding_atr)
    high = rounded(max(item.value for item in levels) + daily_atr * policy.zone_padding_atr)
    families = tuple(dict.fromkeys(item.family for item in levels))
    source_score = min(
        Decimal("70"),
        sum(
            (
                max(item.score_points for item in levels if item.family == family)
                for family in families
            ),
            ZERO,
        ),
    )
    defenses = tuple(
        bar.timestamp.date().isoformat()
        for bar in daily_bars[-120:]
        if abs(bar.low - center) <= daily_atr * policy.defense_distance_atr
        and bar.close > center
    )
    defense_dates = tuple(dict.fromkeys(defenses))[-3:]
    score = min(HUNDRED, source_score + Decimal("10") * len(defense_dates))
    lows, _ = confirmed_pivot_indices(daily_bars)
    structural = (
        daily_bars[lows[-1]].low
        if lows
        else low - daily_atr * policy.invalidation_buffer_atr
    )
    invalidation = rounded(
        min(structural, low - daily_atr * policy.invalidation_buffer_atr)
    )
    return SupportZone(
        low=low,
        center=center,
        high=high,
        invalidation=invalidation,
        atr14=daily_atr,
        score=score,
        sources=tuple(dict.fromkeys(item.name for item in levels)),
        defense_dates=defense_dates,
    )


def _frozen_zone(
    watch: PatreonCapsWatch,
    daily_atr: Decimal,
    daily_bars: tuple[MarketBar, ...],
) -> SupportZone:
    defenses = tuple(
        bar.timestamp.date().isoformat()
        for bar in daily_bars[-120:]
        if abs(bar.low - watch.zone_center) <= daily_atr * Decimal("0.25")
        and bar.close > watch.zone_center
    )
    return SupportZone(
        low=watch.zone_low,
        center=watch.zone_center,
        high=watch.zone_high,
        invalidation=watch.invalidation,
        atr14=daily_atr,
        score=min(
            HUNDRED,
            Decimal("10") * len(tuple(dict.fromkeys(defenses))[-3:]) + Decimal("70"),
        ),
        sources=watch.support_sources,
        defense_dates=tuple(dict.fromkeys(defenses))[-3:],
    )


def _confirmation_score(
    context: PatreonCapsContext, zone: SupportZone
) -> tuple[Decimal, tuple[str, ...]]:
    bars = context.intraday_bars
    if not bars:
        return ZERO, ("intraday_history_missing",)
    latest = bars[-1]
    session_avwap = anchored_vwap(bars, 0)
    reclaim = ZERO
    if latest.close > zone.center:
        reclaim += Decimal("15")
    if latest.close > zone.high:
        reclaim += Decimal("10")
    if latest.close > session_avwap:
        reclaim += Decimal("5")
    rvol = relative_volume(bars, 20)
    volume = (
        Decimal("20")
        if rvol >= Decimal("2")
        else Decimal("15")
        if rvol >= Decimal("1.5")
        else Decimal("8")
        if rvol >= Decimal("1.2")
        else ZERO
    )
    candle = (
        Decimal("15")
        if _bullish_engulfing(bars)
        else Decimal("10")
        if _hammer(latest)
        else ZERO
    )
    persistence = len(bars) >= 2 and all(
        bar.close > min(zone.center, session_avwap) for bar in bars[-2:]
    )
    structure = Decimal("10") if persistence else ZERO
    if _has_higher_low(bars):
        structure += Decimal("10")
    divergence = Decimal("15") if _bullish_rsi_divergence(bars) else ZERO
    score = _score(reclaim + volume + candle + structure + divergence)
    return score, (
        f"support_reclaim_score:{reclaim}",
        f"support_volume_score:{volume}",
        f"support_candle_score:{candle}",
        f"support_structure_score:{structure}",
        f"support_divergence_score:{divergence}",
    )


def _hammer(bar: MarketBar) -> bool:
    body = abs(bar.close - bar.open)
    lower_wick = min(bar.open, bar.close) - bar.low
    location = (bar.close - bar.low) / (bar.high - bar.low) if bar.high > bar.low else ZERO
    return lower_wick >= body * Decimal("2") and location >= Decimal("0.65")


def _bullish_engulfing(bars: tuple[MarketBar, ...]) -> bool:
    if len(bars) < 2:
        return False
    previous = bars[-2]
    current = bars[-1]
    return (
        previous.close < previous.open
        and current.close > current.open
        and current.open <= previous.close
        and current.close >= previous.open
    )


def _has_higher_low(bars: tuple[MarketBar, ...]) -> bool:
    lows, _ = confirmed_pivot_indices(bars)
    return len(lows) >= 2 and bars[lows[-1]].low > bars[lows[-2]].low


def _bullish_rsi_divergence(bars: tuple[MarketBar, ...]) -> bool:
    lows, _ = confirmed_pivot_indices(bars)
    if len(lows) < 2 or lows[-2] < 14 or lows[-1] < 14:
        return False
    earlier, latest = lows[-2:]
    if bars[latest].low > bars[earlier].low:
        return False
    closes = tuple(bar.close for bar in bars)
    return rsi(closes[: latest + 1]) >= rsi(closes[: earlier + 1]) + Decimal("5")


def _touched_zone_current_session(
    bars: tuple[MarketBar, ...], watch: PatreonCapsWatch, zone: SupportZone
) -> bool:
    session_date = bars[-1].timestamp.date()
    padding = zone.atr14 * Decimal("0.15")
    return any(
        bar.timestamp.date() == session_date
        and bar.low <= watch.zone_high + padding
        and bar.high >= watch.zone_low - padding
        for bar in bars
    )


def _base_structure_confirmed(
    bars: tuple[MarketBar, ...], watch: PatreonCapsWatch, zone: SupportZone
) -> bool:
    if any(
        bar.timestamp >= watch.armed_at and bar.close < watch.invalidation
        for bar in bars
    ):
        return False
    defenses = tuple(
        index
        for index, bar in enumerate(bars)
        if abs(bar.low - watch.zone_center) <= zone.atr14 * Decimal("0.25")
        and bar.close > watch.zone_center
    )
    if len(defenses) < 2:
        return False
    first, second = defenses[-2:]
    if second >= len(bars) - 1:
        return False
    if bars[second].low < bars[first].low - zone.atr14 * Decimal("0.15"):
        return False
    base_high = max(bar.high for bar in bars[first : second + 1])
    return any(bar.close > base_high for bar in bars[second + 1 :])


def _continuation_aligned(
    analyses: dict[AnalysisHorizon, AnalysisResult],
) -> bool:
    long = analyses.get(AnalysisHorizon.LONG_TERM)
    swing = analyses.get(AnalysisHorizon.SWING)
    intraday = analyses.get(AnalysisHorizon.INTRADAY)
    return (
        long is not None
        and long.direction is PatternDirection.BULLISH
        and long.verdict in {AnalysisVerdict.WATCH, AnalysisVerdict.FAVORABLE}
        and swing is not None
        and swing.verdict in {AnalysisVerdict.WATCH, AnalysisVerdict.FAVORABLE}
        and _metrics(swing).get("structure_broken_confirmed") is not True
        and intraday is not None
        and intraday.verdict is AnalysisVerdict.FAVORABLE
        and _metrics(intraday).get("confirmation_gate_passed") is True
    )


def _can_arm(
    context: PatreonCapsContext,
    zone: SupportZone,
    price: Decimal,
    analyses: dict[AnalysisHorizon, AnalysisResult],
    policy: PatreonCapsPolicy,
) -> bool:
    long = analyses.get(AnalysisHorizon.LONG_TERM)
    families = {_source_family(source) for source in zone.sources}
    return (
        zone.score >= policy.minimum_confluence_score
        and len(families) >= policy.minimum_source_families
        and zone.invalidation < price <= zone.high + zone.atr14
        and long is not None
        and long.direction is PatternDirection.BULLISH
        and long.verdict in {AnalysisVerdict.WATCH, AnalysisVerdict.FAVORABLE}
    )


def _assessment(
    context: PatreonCapsContext,
    watch: PatreonCapsWatch,
    zone: SupportZone,
    confirmation_score: Decimal,
    alignment_score: Decimal,
    lesson: LessonAssessment,
    patreon_score: Decimal,
    macro_threshold: Decimal | None,
    reasons: tuple[str, ...],
) -> PatreonCapsAssessment:
    return PatreonCapsAssessment(
        symbol=context.symbol,
        occurred_at=context.as_of,
        rule_version=watch.rule_version,
        mode=StrategyMode.SHADOW,
        state=watch.state,
        current_price=_current_price(context),
        zone_low=watch.zone_low,
        zone_center=watch.zone_center,
        zone_high=watch.zone_high,
        invalidation=watch.invalidation,
        atr14=zone.atr14,
        confluence_score=zone.score,
        confirmation_score=confirmation_score,
        alignment_score=alignment_score,
        lesson_score=lesson.score,
        lesson_gate_passed=lesson.gate_passed,
        lesson_reasons=lesson.reasons,
        lesson_metrics=lesson.metrics,
        patreon_score=patreon_score,
        macro_regime=context.macro_regime,
        macro_threshold=macro_threshold,
        macro_signals=context.macro_signals,
        macro_metrics=context.macro_metrics,
        support_sources=watch.support_sources,
        source_analysis_ids=watch.source_analysis_ids,
        reasons=tuple(dict.fromkeys(reasons)) or ("patreon_caps_evaluated",),
    )


def _transition(
    assessment: PatreonCapsAssessment,
    watch: PatreonCapsWatch,
    *,
    previous: PatreonCapsState | None,
    confirmation_type: str | None,
    sizing: TrancheSizing | None,
) -> PatreonCapsTransition:
    return PatreonCapsTransition(
        watch_id=watch.watch_id,
        symbol=watch.symbol,
        previous_state=previous,
        state=watch.state,
        occurred_at=assessment.occurred_at,
        rule_version=watch.rule_version,
        current_price=assessment.current_price,
        zone_low=watch.zone_low,
        zone_center=watch.zone_center,
        zone_high=watch.zone_high,
        invalidation=watch.invalidation,
        confluence_score=assessment.confluence_score,
        confirmation_score=assessment.confirmation_score,
        alignment_score=assessment.alignment_score,
        lesson_score=assessment.lesson_score,
        lesson_gate_passed=assessment.lesson_gate_passed,
        lesson_reasons=assessment.lesson_reasons,
        lesson_metrics=assessment.lesson_metrics,
        patreon_score=assessment.patreon_score,
        macro_regime=assessment.macro_regime,
        macro_signals=assessment.macro_signals,
        macro_metrics=assessment.macro_metrics,
        confirmation_type=confirmation_type,
        tranche_stage=watch.tranche_stage or None,
        suggested_tranche_usd=(sizing.suggested_tranche_usd if sizing else None),
        suggested_whole_shares=(sizing.suggested_whole_shares if sizing else None),
        source_analysis_ids=watch.source_analysis_ids,
        reasons=assessment.reasons,
        expires_at=watch.expires_at,
    )


def _score(value: Decimal) -> Decimal:
    return min(HUNDRED, max(ZERO, value)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


def _source_family(source: str) -> str:
    if source.startswith("pivot_daily"):
        return "pivot_daily"
    if source.startswith("pivot_weekly"):
        return "pivot_weekly"
    if "avwap" in source:
        return "avwap"
    if source.startswith("breakout"):
        return "breakout"
    if source.startswith("daily_sma"):
        return "sma_daily"
    if source.startswith("weekly_sma"):
        return "sma_weekly"
    if source.startswith("fib_"):
        return "fibonacci"
    return "round"
