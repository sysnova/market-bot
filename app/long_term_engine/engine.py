"""Pure long-term analysis engine."""

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
    distribution_weeks,
    has_higher_lows,
    mean,
    percent_vs,
    relative_volume,
    rounded,
    rsi,
    sma,
)
from .models import (
    EntryZoneStatus,
    LongTermAnalysis,
    LongTermBias,
    LongTermClassification,
    LongTermContext,
    LongTermIndicators,
    LongTermLevels,
    Score,
    TrendTemplate,
)

ZERO = Decimal("0")
HUNDRED = Decimal("100")


class LongTermEngine:
    """Analyze completed daily/weekly history without I/O or mutable state."""

    engine_id = "long-term"
    engine_version = "1.0.0"

    def analyze(
        self,
        context: LongTermContext,
        *,
        source_event_ids: tuple[UUID, ...] = (),
    ) -> AnalysisResult:
        """Return the stable cross-engine contract for this context."""

        detail = self.evaluate(context)
        return AnalysisResult(
            engine_id=self.engine_id,
            engine_version=self.engine_version,
            symbol=context.symbol,
            horizon=AnalysisHorizon.LONG_TERM,
            as_of=context.as_of,
            verdict=self._verdict(detail.classification),
            direction=self._direction(detail.bias),
            score=detail.score,
            confidence=detail.setup_score / HUNDRED,
            reasons=detail.reasons + detail.risk_flags,
            metrics=self._metrics(detail),
            source_event_ids=source_event_ids,
            context_hash=f"sha256:{sha256_digest(context.model_dump(mode='python'))}",
        )

    def evaluate(self, context: LongTermContext) -> LongTermAnalysis:
        """Return the engine-owned detailed calculation without transport metadata."""

        if len(context.daily_bars) < 200 or len(context.weekly_bars) < 50:
            return LongTermAnalysis(
                symbol=context.symbol,
                as_of=context.as_of,
                score=ZERO,
                setup_score=ZERO,
                entry_score=ZERO,
                classification=LongTermClassification.INSUFFICIENT_DATA,
                bias=LongTermBias.UNKNOWN,
                indicators=None,
                levels=None,
                reasons=("insufficient_history",),
                risk_flags=("insufficient_history",),
            )

        indicators = self._indicators(context)
        levels = self._levels(context, indicators)
        setup_score = self._setup_score(indicators)
        entry_score = self._entry_score(indicators, levels)
        score = _score(setup_score * Decimal("0.65") + entry_score * Decimal("0.35"))
        classification = self._classify(indicators, levels, setup_score, entry_score)
        bias = self._bias(classification, indicators)
        reasons = self._reasons(indicators, levels)
        risk_flags = self._risk_flags(indicators, levels, classification)
        return LongTermAnalysis(
            symbol=context.symbol,
            as_of=context.as_of,
            score=score,
            setup_score=setup_score,
            entry_score=entry_score,
            classification=classification,
            bias=bias,
            indicators=indicators,
            levels=levels,
            reasons=reasons,
            risk_flags=risk_flags,
        )

    @staticmethod
    def _verdict(classification: LongTermClassification) -> AnalysisVerdict:
        return {
            LongTermClassification.BUY_ZONE: AnalysisVerdict.FAVORABLE,
            LongTermClassification.SETUP: AnalysisVerdict.WATCH,
            LongTermClassification.WATCH_PULLBACK: AnalysisVerdict.WATCH,
            LongTermClassification.EXTENDED: AnalysisVerdict.CAUTION,
            LongTermClassification.AVOID: AnalysisVerdict.AVOID,
            LongTermClassification.INSUFFICIENT_DATA: AnalysisVerdict.INSUFFICIENT_DATA,
        }[classification]

    @staticmethod
    def _direction(bias: LongTermBias) -> PatternDirection:
        return {
            LongTermBias.BULLISH: PatternDirection.BULLISH,
            LongTermBias.BEARISH: PatternDirection.BEARISH,
            LongTermBias.NEUTRAL: PatternDirection.NEUTRAL,
            LongTermBias.UNKNOWN: PatternDirection.NEUTRAL,
        }[bias]

    @staticmethod
    def _metrics(detail: LongTermAnalysis) -> tuple[NamedValue, ...]:
        values: list[NamedValue] = [
            NamedValue(name="classification", value=detail.classification.value),
            NamedValue(name="setup_score", value=detail.setup_score),
            NamedValue(name="entry_score", value=detail.entry_score),
            NamedValue(name="risk_flags", value=detail.risk_flags),
        ]
        if detail.indicators is not None:
            values.extend(
                (
                    NamedValue(name="daily_rsi14", value=detail.indicators.daily_rsi14),
                    NamedValue(name="weekly_rsi14", value=detail.indicators.weekly_rsi14),
                    NamedValue(name="daily_rvol20", value=detail.indicators.daily_rvol20),
                    NamedValue(name="weekly_rvol10", value=detail.indicators.weekly_rvol10),
                    NamedValue(
                        name="trend_template_score",
                        value=detail.indicators.trend_template.score,
                    ),
                )
            )
        if detail.levels is not None:
            values.extend(
                (
                    NamedValue(name="support", value=detail.levels.support),
                    NamedValue(name="resistance", value=detail.levels.resistance),
                    NamedValue(name="buy_zone_low", value=detail.levels.buy_zone_low),
                    NamedValue(name="buy_zone_high", value=detail.levels.buy_zone_high),
                    NamedValue(name="invalidation", value=detail.levels.invalidation),
                )
            )
        return tuple(values)

    @staticmethod
    def _indicators(context: LongTermContext) -> LongTermIndicators:
        daily_closes = tuple(bar.close for bar in context.daily_bars)
        weekly_closes = tuple(bar.close for bar in context.weekly_bars)
        daily_sma20 = sma(daily_closes, 20)
        daily_sma50 = sma(daily_closes, 50)
        daily_sma150 = sma(daily_closes, 150)
        daily_sma200 = sma(daily_closes, 200)
        weekly_sma10 = sma(weekly_closes, 10)
        weekly_sma30 = sma(weekly_closes, 30)
        weekly_sma50 = sma(weekly_closes, 50)
        previous_daily_sma200 = sma(daily_closes[:-20], 200) if len(daily_closes) >= 220 else None
        previous_weekly_sma30 = sma(weekly_closes[:-4], 30)
        previous_weekly_sma50 = (
            sma(weekly_closes[:-8], 50) if len(weekly_closes) >= 58 else weekly_sma50
        )
        high52 = max(bar.high for bar in context.weekly_bars[-52:])
        low52 = min(bar.low for bar in context.weekly_bars[-52:])
        criteria = (
            ("price_above_sma150", context.price > daily_sma150),
            ("price_above_sma200", context.price > daily_sma200),
            ("sma150_above_sma200", daily_sma150 > daily_sma200),
            (
                "sma200_rising",
                previous_daily_sma200 is None or daily_sma200 > previous_daily_sma200,
            ),
            ("price_above_sma50", context.price > daily_sma50),
            (
                "sma50_above_sma150_sma200",
                daily_sma50 > daily_sma150 and daily_sma50 > daily_sma200,
            ),
            ("price_30pct_above_52w_low", context.price >= low52 * Decimal("1.3")),
            ("price_within_25pct_52w_high", context.price >= high52 * Decimal("0.75")),
        )
        passed = tuple(name for name, result in criteria if result)
        failed = tuple(name for name, result in criteria if not result)
        weekly_average_volume = mean(
            tuple(Decimal(bar.volume) for bar in context.weekly_bars[-11:-1])
        )
        return LongTermIndicators(
            daily_sma20=daily_sma20,
            daily_sma50=daily_sma50,
            daily_sma150=daily_sma150,
            daily_sma200=daily_sma200,
            weekly_sma10=weekly_sma10,
            weekly_sma30=weekly_sma30,
            weekly_sma50=weekly_sma50,
            daily_rsi14=rsi(daily_closes),
            weekly_rsi14=rsi(weekly_closes),
            daily_rvol20=relative_volume(context.daily_bars, 20),
            weekly_rvol10=relative_volume(context.weekly_bars, 10),
            daily_price_vs_sma50_percent=percent_vs(context.price, daily_sma50),
            weekly_price_vs_sma10_percent=percent_vs(context.price, weekly_sma10),
            weekly_price_vs_sma30_percent=percent_vs(context.price, weekly_sma30),
            weekly_price_vs_sma50_percent=percent_vs(context.price, weekly_sma50),
            weekly_sma30_slope_percent=percent_vs(weekly_sma30, previous_weekly_sma30),
            weekly_sma50_slope_percent=percent_vs(weekly_sma50, previous_weekly_sma50),
            distance_to_high52_percent=rounded(
                ((high52 - context.price) / context.price) * HUNDRED
            ),
            distance_from_low52_percent=percent_vs(context.price, low52),
            distribution_weeks=distribution_weeks(context.weekly_bars, weekly_average_volume),
            higher_weekly_lows=has_higher_lows(context.weekly_bars),
            trend_template=TrendTemplate(
                score=_score(Decimal(len(passed)) / Decimal(len(criteria)) * HUNDRED),
                passed=len(passed) == len(criteria),
                passed_criteria=passed,
                failed_criteria=failed,
            ),
        )

    @staticmethod
    def _levels(context: LongTermContext, indicators: LongTermIndicators) -> LongTermLevels:
        recent = context.weekly_bars[-13:]
        support = min(bar.low for bar in recent)
        prior = context.weekly_bars[-14:-1]
        resistance = max(bar.high for bar in prior)
        high52 = max(bar.high for bar in context.weekly_bars[-52:])
        low52 = min(bar.low for bar in context.weekly_bars[-52:])
        reference = context.weekly_bars[-1].close
        supports = (
            support,
            indicators.weekly_sma10,
            indicators.weekly_sma30,
            indicators.weekly_sma50,
        )
        floor = min(supports)
        preferred_low = max(floor, reference * Decimal("0.82"))
        high = min(
            indicators.weekly_sma10 * Decimal("1.05"),
            indicators.weekly_sma30 * Decimal("1.08"),
            reference * Decimal("1.02"),
        )
        fallback_low = max(floor, high * Decimal("0.92"))
        low = min(high, preferred_low if preferred_low <= high else fallback_low)
        invalidation = min(support, indicators.weekly_sma30, indicators.weekly_sma50) * Decimal(
            "0.97"
        )
        if context.price > high:
            status = EntryZoneStatus.ABOVE_BUY_ZONE
        elif context.price < low:
            status = EntryZoneStatus.BELOW_BUY_ZONE
        else:
            status = EntryZoneStatus.IN_BUY_ZONE
        return LongTermLevels(
            support=rounded(support),
            resistance=rounded(resistance),
            high52=rounded(high52),
            low52=rounded(low52),
            buy_zone_low=rounded(low),
            buy_zone_high=rounded(high),
            invalidation=rounded(invalidation),
            entry_zone_status=status,
            levels_as_of=context.weekly_bars[-1].timestamp,
        )

    @staticmethod
    def _setup_score(indicators: LongTermIndicators) -> Score:
        score = indicators.trend_template.score * Decimal("0.3")
        score += Decimal("12") if indicators.weekly_price_vs_sma30_percent >= 0 else Decimal("-8")
        score += Decimal("12") if indicators.weekly_price_vs_sma50_percent >= 0 else Decimal("-6")
        score += Decimal("8") if indicators.weekly_sma30_slope_percent >= 0 else Decimal("-4")
        score += Decimal("6") if indicators.weekly_sma50_slope_percent >= 0 else Decimal("-4")
        if Decimal("50") <= indicators.weekly_rsi14 <= Decimal("72"):
            score += Decimal("10")
        elif Decimal("45") <= indicators.weekly_rsi14 < Decimal("50"):
            score += Decimal("5")
        if indicators.higher_weekly_lows:
            score += Decimal("8")
        if indicators.weekly_rvol10 is not None:
            score += Decimal("6") if indicators.weekly_rvol10 >= Decimal("1.1") else Decimal("3")
        score += Decimal("6") if indicators.distribution_weeks == 0 else Decimal("-8")
        return _score(score)

    @staticmethod
    def _entry_score(indicators: LongTermIndicators, levels: LongTermLevels) -> Score:
        score = Decimal("45")
        if levels.entry_zone_status is EntryZoneStatus.IN_BUY_ZONE:
            score += Decimal("20")
        elif levels.entry_zone_status is EntryZoneStatus.ABOVE_BUY_ZONE:
            score -= Decimal("10")
        else:
            score -= Decimal("8")
        if Decimal("-4") <= indicators.weekly_price_vs_sma10_percent <= Decimal("5"):
            score += Decimal("14")
        elif Decimal("-3") <= indicators.weekly_price_vs_sma30_percent <= Decimal("8"):
            score += Decimal("10")
        if Decimal("-2") <= indicators.daily_price_vs_sma50_percent <= Decimal("8"):
            score += Decimal("10")
        if (
            indicators.weekly_price_vs_sma30_percent > Decimal("18")
            or indicators.weekly_rsi14 >= Decimal("74")
        ):
            score -= Decimal("25")
        if indicators.distribution_weeks >= 2:
            score -= Decimal("10")
        return _score(score)

    @staticmethod
    def _classify(
        indicators: LongTermIndicators,
        levels: LongTermLevels,
        setup_score: Score,
        entry_score: Score,
    ) -> LongTermClassification:
        broken = (
            indicators.weekly_price_vs_sma30_percent < 0
            and indicators.weekly_price_vs_sma50_percent < 0
            and indicators.weekly_sma30_slope_percent < 0
            and indicators.weekly_rsi14 < Decimal("45")
        )
        distribution_breakdown = (
            indicators.distribution_weeks >= 2
            and indicators.weekly_price_vs_sma30_percent < 0
        )
        if broken or distribution_breakdown:
            return LongTermClassification.AVOID
        if (
            indicators.weekly_price_vs_sma30_percent > Decimal("20")
            or indicators.weekly_price_vs_sma50_percent > Decimal("30")
            or (
                indicators.weekly_rsi14 >= Decimal("75")
                and indicators.weekly_price_vs_sma10_percent > Decimal("8")
            )
        ):
            return LongTermClassification.EXTENDED
        constructive = (
            setup_score >= Decimal("68")
            and indicators.weekly_price_vs_sma30_percent >= 0
            and indicators.weekly_price_vs_sma50_percent >= 0
            and indicators.weekly_sma30_slope_percent >= 0
        )
        in_eligible_zone = (
            levels.entry_zone_status is EntryZoneStatus.IN_BUY_ZONE
            and entry_score >= Decimal("60")
        )
        if constructive and in_eligible_zone:
            return LongTermClassification.BUY_ZONE
        if constructive and levels.entry_zone_status is EntryZoneStatus.ABOVE_BUY_ZONE:
            return LongTermClassification.WATCH_PULLBACK
        if constructive:
            return LongTermClassification.SETUP
        return LongTermClassification.WATCH_PULLBACK

    @staticmethod
    def _bias(
        classification: LongTermClassification, indicators: LongTermIndicators
    ) -> LongTermBias:
        if classification is LongTermClassification.AVOID:
            return LongTermBias.BEARISH
        if classification in {
            LongTermClassification.BUY_ZONE,
            LongTermClassification.SETUP,
            LongTermClassification.EXTENDED,
        }:
            return LongTermBias.BULLISH
        if indicators.weekly_price_vs_sma30_percent >= 0:
            return LongTermBias.BULLISH
        return LongTermBias.NEUTRAL

    @staticmethod
    def _reasons(
        indicators: LongTermIndicators, levels: LongTermLevels
    ) -> tuple[str, ...]:
        reasons: list[str] = []
        if indicators.trend_template.passed:
            reasons.append("trend_template_passed")
        if indicators.weekly_price_vs_sma30_percent >= 0:
            reasons.append("weekly_above_30w")
        if indicators.weekly_price_vs_sma50_percent >= 0:
            reasons.append("weekly_above_50w")
        if indicators.weekly_sma30_slope_percent >= 0:
            reasons.append("weekly_30w_rising")
        if Decimal("50") <= indicators.weekly_rsi14 <= Decimal("72"):
            reasons.append("weekly_rsi_constructive")
        if indicators.higher_weekly_lows:
            reasons.append("higher_weekly_lows")
        if levels.entry_zone_status is EntryZoneStatus.IN_BUY_ZONE:
            reasons.append("in_weekly_buy_zone")
        if indicators.distribution_weeks == 0:
            reasons.append("no_recent_weekly_distribution")
        return tuple(reasons) or ("no_constructive_factor",)

    @staticmethod
    def _risk_flags(
        indicators: LongTermIndicators,
        levels: LongTermLevels,
        classification: LongTermClassification,
    ) -> tuple[str, ...]:
        flags = [
            f"trend_template_failed:{name}"
            for name in indicators.trend_template.failed_criteria
        ]
        if levels.entry_zone_status is EntryZoneStatus.ABOVE_BUY_ZONE:
            flags.append("above_weekly_buy_zone")
        if levels.entry_zone_status is EntryZoneStatus.BELOW_BUY_ZONE:
            flags.append("below_weekly_buy_zone")
        if indicators.distribution_weeks >= 2:
            flags.append("weekly_distribution")
        if indicators.weekly_rsi14 >= Decimal("75"):
            flags.append("weekly_rsi_hot")
        if indicators.weekly_price_vs_sma30_percent > Decimal("20"):
            flags.append("extended_from_30w")
        if indicators.weekly_price_vs_sma50_percent > Decimal("30"):
            flags.append("extended_from_50w")
        if classification is LongTermClassification.AVOID:
            flags.append("weekly_structure_broken")
        return tuple(flags)


def _score(value: Decimal) -> Score:
    bounded = min(HUNDRED, max(ZERO, value))
    return bounded.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
