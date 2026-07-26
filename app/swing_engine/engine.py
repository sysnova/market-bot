"""Pure swing-horizon analysis engine."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from app.common.canonical import sha256_digest
from app.contracts import (
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    NamedValue,
    PatternDirection,
)

from .indicators import (
    anchored_vwap,
    atr,
    last_breakout_index,
    last_confirmed_pivot_low_index,
    percent_vs,
    relative_volume,
    rounded,
    rsi,
    sma,
)
from .models import (
    Score,
    SwingAnalysis,
    SwingClassification,
    SwingContext,
    SwingIndicators,
    SwingLevels,
)

ZERO = Decimal("0")
HUNDRED = Decimal("100")


class SwingEngine:
    """Evaluate swing structure and entry asymmetry without side effects."""

    engine_id = "swing"
    engine_version = "1.1.1"

    def analyze(
        self,
        context: SwingContext,
        *,
        source_event_ids: tuple[UUID, ...] = (),
    ) -> AnalysisResult:
        detail = self.evaluate(context)
        return AnalysisResult(
            engine_id=self.engine_id,
            engine_version=self.engine_version,
            symbol=context.symbol,
            horizon=AnalysisHorizon.SWING,
            as_of=context.as_of,
            verdict=self._verdict(detail.classification, detail.levels),
            direction=self._direction(detail.classification),
            score=detail.score,
            confidence=detail.score / HUNDRED,
            reasons=detail.reasons + detail.risk_flags,
            metrics=self._metrics(detail, reference_price=context.price),
            source_event_ids=source_event_ids,
            context_hash=f"sha256:{sha256_digest(context.model_dump(mode='python'))}",
        )

    def evaluate(self, context: SwingContext) -> SwingAnalysis:
        if len(context.daily_bars) < 50 or len(context.intraday_bars) < 21:
            return SwingAnalysis(
                symbol=context.symbol,
                as_of=context.as_of,
                score=ZERO,
                classification=SwingClassification.INSUFFICIENT_DATA,
                indicators=None,
                levels=None,
                reasons=("insufficient_history",),
                risk_flags=("insufficient_history",),
            )
        indicators = self._indicators(context)
        levels = self._levels(context, indicators)
        classification = self._classify(indicators, levels)
        score = self._score(indicators, levels, classification)
        return SwingAnalysis(
            symbol=context.symbol,
            as_of=context.as_of,
            score=score,
            classification=classification,
            indicators=indicators,
            levels=levels,
            reasons=self._reasons(indicators, classification),
            risk_flags=self._risk_flags(indicators, levels, classification),
        )

    @staticmethod
    def _indicators(context: SwingContext) -> SwingIndicators:
        closes = tuple(bar.close for bar in context.daily_bars)
        daily_sma20 = sma(closes, 20)
        daily_sma50 = sma(closes, 50)
        previous_sma20 = sma(closes[:-5], 20)
        atr14 = atr(context.daily_bars)
        resistance = max(bar.high for bar in context.daily_bars[-21:-1])
        pivot_low_index = last_confirmed_pivot_low_index(context.daily_bars)
        breakout_index = last_breakout_index(context.daily_bars)
        pivot_low_avwap = (
            anchored_vwap(context.daily_bars, pivot_low_index)
            if pivot_low_index is not None
            else None
        )
        breakout_avwap = (
            anchored_vwap(context.daily_bars, breakout_index)
            if breakout_index is not None
            else None
        )
        daily_rvol = relative_volume(context.daily_bars)
        intraday_rvol = relative_volume(context.intraday_bars)
        bullish_trend = (
            context.price > daily_sma50
            and daily_sma20 > daily_sma50
            and daily_sma20 >= previous_sma20
        )
        bearish_trend = context.price < daily_sma50 and daily_sma20 < daily_sma50
        breakout_location = context.price >= resistance * Decimal("1.003")
        volume_confirmed = (daily_rvol or ZERO) >= Decimal("1.2") or (
            intraday_rvol or ZERO
        ) >= Decimal("1.2")
        return SwingIndicators(
            daily_sma20=daily_sma20,
            daily_sma50=daily_sma50,
            daily_sma20_slope_percent=percent_vs(daily_sma20, previous_sma20),
            daily_rsi14=rsi(closes),
            atr14=atr14,
            atr_percent=rounded(atr14 / context.price * HUNDRED),
            daily_rvol20=daily_rvol,
            intraday_rvol20=intraday_rvol,
            price_vs_sma20_percent=percent_vs(context.price, daily_sma20),
            price_vs_sma50_percent=percent_vs(context.price, daily_sma50),
            price_vs_resistance_percent=percent_vs(context.price, resistance),
            pivot_low_anchor_at=(
                context.daily_bars[pivot_low_index].timestamp
                if pivot_low_index is not None
                else None
            ),
            pivot_low_avwap=pivot_low_avwap,
            price_vs_pivot_low_avwap_percent=(
                percent_vs(context.price, pivot_low_avwap)
                if pivot_low_avwap is not None
                else None
            ),
            breakout_anchor_at=(
                context.daily_bars[breakout_index].timestamp
                if breakout_index is not None
                else None
            ),
            breakout_avwap=breakout_avwap,
            price_vs_breakout_avwap_percent=(
                percent_vs(context.price, breakout_avwap)
                if breakout_avwap is not None
                else None
            ),
            bullish_trend=bullish_trend,
            bearish_trend=bearish_trend,
            breakout_location=breakout_location,
            volume_confirmed=volume_confirmed,
        )

    @staticmethod
    def _levels(context: SwingContext, indicators: SwingIndicators) -> SwingLevels:
        support = min(bar.low for bar in context.daily_bars[-10:])
        resistance = max(bar.high for bar in context.daily_bars[-21:-1])
        candidates = tuple(
            value
            for value in (
                support,
                indicators.daily_sma20,
                indicators.daily_sma50,
                resistance,
                indicators.pivot_low_avwap,
                indicators.breakout_avwap,
            )
            if value is not None and value < context.price
        )
        technical_support = max(candidates) if candidates else context.price - indicators.atr14
        invalidation = rounded(technical_support * Decimal("0.985"))
        if invalidation >= context.price:
            invalidation = rounded(context.price - indicators.atr14)
        risk = context.price - invalidation
        target = rounded(context.price + risk * Decimal("2"))
        risk_percent = rounded(risk / context.price * HUNDRED)
        risk_atr = rounded(risk / indicators.atr14)
        risk_ok = risk > 0 and risk_percent <= Decimal("8") and risk_atr <= Decimal("3")
        return SwingLevels(
            support=rounded(support),
            resistance=rounded(resistance),
            invalidation=invalidation,
            target=target,
            risk_percent=risk_percent,
            risk_atr=risk_atr,
            risk_ok=risk_ok,
            levels_as_of=context.daily_bars[-1].timestamp,
        )

    @staticmethod
    def _classify(
        indicators: SwingIndicators, levels: SwingLevels
    ) -> SwingClassification:
        if indicators.bearish_trend or indicators.price_vs_sma50_percent <= Decimal("-5"):
            return SwingClassification.AVOID
        if indicators.bullish_trend and indicators.breakout_location:
            if indicators.volume_confirmed and levels.risk_ok:
                return SwingClassification.BREAKOUT
            return SwingClassification.SETUP
        near_sma20 = abs(indicators.price_vs_sma20_percent) <= Decimal("3")
        if indicators.bullish_trend and near_sma20 and levels.risk_ok:
            return SwingClassification.PULLBACK
        if indicators.bullish_trend and indicators.price_vs_sma20_percent > Decimal("10"):
            return SwingClassification.EXTENDED
        return SwingClassification.SETUP

    @staticmethod
    def _score(
        indicators: SwingIndicators,
        levels: SwingLevels,
        classification: SwingClassification,
    ) -> Score:
        if classification is SwingClassification.AVOID:
            return Decimal("10.00")
        score = Decimal("30") if indicators.bullish_trend else Decimal("8")
        score += {
            SwingClassification.BREAKOUT: Decimal("30"),
            SwingClassification.PULLBACK: Decimal("28"),
            SwingClassification.SETUP: Decimal("14"),
            SwingClassification.EXTENDED: Decimal("12"),
            SwingClassification.AVOID: ZERO,
            SwingClassification.INSUFFICIENT_DATA: ZERO,
        }[classification]
        if (indicators.intraday_rvol20 or ZERO) >= Decimal("1.5"):
            score += Decimal("18")
        elif indicators.volume_confirmed:
            score += Decimal("14")
        else:
            score += Decimal("6")
        score += Decimal("22") if levels.risk_ok else Decimal("4")
        for distance in (
            indicators.price_vs_pivot_low_avwap_percent,
            indicators.price_vs_breakout_avwap_percent,
        ):
            if distance is not None:
                score += Decimal("6") if distance >= ZERO else Decimal("-10")
        hot_and_extended = (
            indicators.daily_rsi14 >= Decimal("78")
            and classification is SwingClassification.EXTENDED
        )
        if hot_and_extended:
            score -= Decimal("10")
        return _score(score)

    @staticmethod
    def _reasons(
        indicators: SwingIndicators, classification: SwingClassification
    ) -> tuple[str, ...]:
        reasons: list[str] = []
        if indicators.bullish_trend:
            reasons.append("bullish_daily_trend")
        if indicators.bearish_trend:
            reasons.append("bearish_daily_trend")
        if classification is SwingClassification.PULLBACK:
            reasons.append("pullback_near_20d")
        if indicators.breakout_location:
            reasons.append("above_20d_resistance")
        if indicators.volume_confirmed:
            reasons.append("relative_volume_confirmed")
        if (
            indicators.price_vs_pivot_low_avwap_percent is not None
            and indicators.price_vs_pivot_low_avwap_percent >= ZERO
        ):
            reasons.append("above_pivot_low_avwap")
        if (
            indicators.price_vs_breakout_avwap_percent is not None
            and indicators.price_vs_breakout_avwap_percent >= ZERO
        ):
            reasons.append("above_breakout_avwap")
        return tuple(reasons) or ("neutral_swing_structure",)

    @staticmethod
    def _risk_flags(
        indicators: SwingIndicators,
        levels: SwingLevels,
        classification: SwingClassification,
    ) -> tuple[str, ...]:
        flags: list[str] = []
        if indicators.breakout_location and not indicators.volume_confirmed:
            flags.append("breakout_without_volume")
        if not levels.risk_ok:
            flags.append("invalidation_risk_too_wide")
        if classification is SwingClassification.EXTENDED:
            flags.append("extended_from_20d")
        if classification is SwingClassification.AVOID:
            flags.append("broken_daily_structure")
        if (
            indicators.price_vs_pivot_low_avwap_percent is not None
            and indicators.price_vs_pivot_low_avwap_percent < ZERO
        ):
            flags.append("below_pivot_low_avwap")
        if (
            indicators.price_vs_breakout_avwap_percent is not None
            and indicators.price_vs_breakout_avwap_percent < ZERO
        ):
            flags.append("below_breakout_avwap")
        return tuple(flags)

    @staticmethod
    def _verdict(
        classification: SwingClassification, levels: SwingLevels | None
    ) -> AnalysisVerdict:
        if classification is SwingClassification.INSUFFICIENT_DATA:
            return AnalysisVerdict.INSUFFICIENT_DATA
        if classification is SwingClassification.AVOID:
            return AnalysisVerdict.AVOID
        if classification is SwingClassification.EXTENDED:
            return AnalysisVerdict.CAUTION
        if classification in {SwingClassification.BREAKOUT, SwingClassification.PULLBACK}:
            if levels and levels.risk_ok:
                return AnalysisVerdict.FAVORABLE
            return AnalysisVerdict.CAUTION
        return AnalysisVerdict.WATCH

    @staticmethod
    def _direction(classification: SwingClassification) -> PatternDirection:
        if classification is SwingClassification.AVOID:
            return PatternDirection.BEARISH
        if classification is SwingClassification.INSUFFICIENT_DATA:
            return PatternDirection.NEUTRAL
        return PatternDirection.BULLISH

    @staticmethod
    def _metrics(
        detail: SwingAnalysis, *, reference_price: Decimal
    ) -> tuple[NamedValue, ...]:
        values: list[NamedValue] = [
            NamedValue(name="classification", value=detail.classification.value),
            NamedValue(name="reference_price", value=reference_price),
            NamedValue(name="risk_flags", value=detail.risk_flags),
        ]
        if detail.indicators is not None:
            values.extend(
                (
                    NamedValue(name="daily_sma20", value=detail.indicators.daily_sma20),
                    NamedValue(name="daily_sma50", value=detail.indicators.daily_sma50),
                    NamedValue(name="daily_rsi14", value=detail.indicators.daily_rsi14),
                    NamedValue(name="atr14", value=detail.indicators.atr14),
                    NamedValue(name="daily_rvol20", value=detail.indicators.daily_rvol20),
                    NamedValue(
                        name="intraday_rvol20", value=detail.indicators.intraday_rvol20
                    ),
                    NamedValue(
                        name="pivot_low_anchor_at",
                        value=detail.indicators.pivot_low_anchor_at,
                    ),
                    NamedValue(
                        name="pivot_low_avwap", value=detail.indicators.pivot_low_avwap
                    ),
                    NamedValue(
                        name="price_vs_pivot_low_avwap_percent",
                        value=detail.indicators.price_vs_pivot_low_avwap_percent,
                    ),
                    NamedValue(
                        name="breakout_anchor_at",
                        value=detail.indicators.breakout_anchor_at,
                    ),
                    NamedValue(
                        name="breakout_avwap", value=detail.indicators.breakout_avwap
                    ),
                    NamedValue(
                        name="price_vs_breakout_avwap_percent",
                        value=detail.indicators.price_vs_breakout_avwap_percent,
                    ),
                )
            )
        if detail.levels is not None:
            values.extend(
                (
                    NamedValue(name="support", value=detail.levels.support),
                    NamedValue(name="resistance", value=detail.levels.resistance),
                    NamedValue(name="invalidation", value=detail.levels.invalidation),
                    NamedValue(name="target_2r", value=detail.levels.target),
                    NamedValue(name="risk_percent", value=detail.levels.risk_percent),
                    NamedValue(name="risk_atr", value=detail.levels.risk_atr),
                )
            )
        return tuple(values)


def _score(value: Decimal) -> Score:
    return min(HUNDRED, max(ZERO, value)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
