"""Deterministic and auditable Intraday v1 analysis."""

from __future__ import annotations

import hashlib
from datetime import datetime
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

from .indicators import atr, ema, percent_vs, relative_volume, rounded, session_vwap
from .models import (
    IntradayAnalysis,
    IntradayContext,
    IntradayIndicators,
    IntradayLevels,
    IntradaySetup,
)

ZERO = Decimal("0")
HUNDRED = Decimal("100")
MINUTE_HISTORY = 30


class IntradayEngine:
    """Analyze normalized completed bars without I/O, clocks or execution state."""

    engine_id = "intraday"
    engine_version = "1.0.0"

    def analyze(
        self,
        context: IntradayContext,
        *,
        source_event_ids: tuple[UUID, ...] = (),
    ) -> AnalysisResult:
        detail = self.evaluate(context)
        context_digest = sha256_digest(context.model_dump(mode="python"))
        return AnalysisResult(
            analysis_id=_stable_analysis_id(context.as_of, context_digest),
            engine_id=self.engine_id,
            engine_version=self.engine_version,
            symbol=context.symbol,
            horizon=AnalysisHorizon.INTRADAY,
            as_of=context.as_of,
            verdict=self._verdict(detail),
            direction=self._direction(detail.setup),
            score=detail.score,
            confidence=detail.confidence,
            reasons=detail.reasons + detail.risk_flags,
            metrics=self._metrics(detail),
            source_event_ids=source_event_ids,
            context_hash=f"sha256:{context_digest}",
        )

    def evaluate(self, context: IntradayContext) -> IntradayAnalysis:
        if len(context.minute_bars) < MINUTE_HISTORY:
            return IntradayAnalysis(
                symbol=context.symbol,
                as_of=context.as_of,
                setup=IntradaySetup.INSUFFICIENT_DATA,
                score=ZERO,
                confidence=ZERO,
                indicators=None,
                levels=None,
                reasons=(f"insufficient_1m_history:{len(context.minute_bars)}/{MINUTE_HISTORY}",),
                risk_flags=("insufficient_history",),
            )

        indicators = self._indicators(context)
        setup = self._setup(context, indicators)
        levels = self._levels(context, indicators, setup)
        score = self._score(indicators, setup, levels)
        risk_flags = self._risk_flags(indicators, setup, levels)
        confidence = (score / HUNDRED).quantize(Decimal("0.0001"))
        return IntradayAnalysis(
            symbol=context.symbol,
            as_of=context.as_of,
            setup=setup,
            score=score,
            confidence=confidence,
            indicators=indicators,
            levels=levels,
            reasons=self._reasons(indicators, setup),
            risk_flags=risk_flags,
        )

    @staticmethod
    def _indicators(context: IntradayContext) -> IntradayIndicators:
        bars = context.minute_bars
        closes = tuple(bar.close for bar in bars)
        price = closes[-1]
        current_vwap = session_vwap(bars)
        previous_vwap = session_vwap(bars[:-1])
        current_atr = atr(bars)
        prior_range = bars[-21:-1]
        five_bias = "unavailable"
        if len(context.five_minute_bars) >= 5:
            five_closes = tuple(bar.close for bar in context.five_minute_bars)
            five_bias = "bullish" if ema(five_closes, 9) >= ema(five_closes, 20) else "bearish"
        return IntradayIndicators(
            price=price,
            session_vwap=rounded(current_vwap),
            previous_session_vwap=rounded(previous_vwap),
            relative_volume=rounded(relative_volume(bars)),
            ema9=rounded(ema(closes, 9)),
            ema20=rounded(ema(closes, 20)),
            momentum_5_percent=rounded(percent_vs(price, closes[-6])),
            atr14=rounded(current_atr),
            atr_percent=rounded(abs(percent_vs(price + current_atr, price))),
            prior_range_high=max(bar.high for bar in prior_range),
            prior_range_low=min(bar.low for bar in prior_range),
            price_vs_vwap_percent=rounded(percent_vs(price, current_vwap)),
            five_minute_bias=five_bias,
        )

    @staticmethod
    def _setup(
        context: IntradayContext, indicators: IntradayIndicators
    ) -> IntradaySetup:
        latest = context.minute_bars[-1]
        previous = context.minute_bars[-2]
        liquid = indicators.relative_volume >= Decimal("1.2")
        if (
            latest.close > indicators.prior_range_high
            and latest.close > latest.open
            and latest.close > indicators.session_vwap
            and indicators.ema9 > indicators.ema20
            and indicators.momentum_5_percent > 0
            and liquid
        ):
            return IntradaySetup.BULLISH_BREAKOUT
        if (
            latest.close < indicators.prior_range_low
            and latest.close < latest.open
            and latest.close < indicators.session_vwap
            and indicators.ema9 < indicators.ema20
            and indicators.momentum_5_percent < 0
            and liquid
        ):
            return IntradaySetup.BEARISH_BREAKDOWN
        if (
            previous.close <= indicators.previous_session_vwap
            and latest.close > indicators.session_vwap
            and latest.close > latest.open
            and indicators.relative_volume >= Decimal("1.05")
        ):
            return IntradaySetup.BULLISH_VWAP_RECLAIM
        if (
            previous.close >= indicators.previous_session_vwap
            and latest.close < indicators.session_vwap
            and latest.close < latest.open
            and indicators.relative_volume >= Decimal("1.05")
        ):
            return IntradaySetup.BEARISH_VWAP_REJECTION
        return IntradaySetup.NO_TRIGGER

    @staticmethod
    def _levels(
        context: IntradayContext,
        indicators: IntradayIndicators,
        setup: IntradaySetup,
    ) -> IntradayLevels | None:
        if setup in {IntradaySetup.NO_TRIGGER, IntradaySetup.INSUFFICIENT_DATA}:
            return None
        latest = context.minute_bars[-1]
        bullish = setup in {
            IntradaySetup.BULLISH_BREAKOUT,
            IntradaySetup.BULLISH_VWAP_RECLAIM,
        }
        if bullish:
            structural = (
                indicators.prior_range_high
                if setup is IntradaySetup.BULLISH_BREAKOUT
                else indicators.session_vwap
            )
            invalidation = max(latest.low, structural - indicators.atr14 * Decimal("0.25"))
            risk = latest.close - invalidation
        else:
            structural = (
                indicators.prior_range_low
                if setup is IntradaySetup.BEARISH_BREAKDOWN
                else indicators.session_vwap
            )
            invalidation = min(latest.high, structural + indicators.atr14 * Decimal("0.25"))
            risk = invalidation - latest.close
        if risk <= 0:
            risk = indicators.atr14 * Decimal("0.25")
            invalidation = latest.close - risk if bullish else latest.close + risk
        reward_risk = Decimal("1.5")
        objective = (
            latest.close + risk * reward_risk
            if bullish
            else latest.close - risk * reward_risk
        )
        if objective <= 0:
            objective = latest.close * Decimal("0.5")
        risk_percent = abs(percent_vs(invalidation, latest.close))
        return IntradayLevels(
            reference_price=rounded(latest.close),
            invalidation_level=rounded(invalidation),
            objective_level=rounded(objective),
            risk_percent=rounded(risk_percent),
            reward_risk_ratio=reward_risk,
            risk_ok=Decimal("0.03") <= risk_percent <= Decimal("1.5"),
        )

    @staticmethod
    def _score(
        indicators: IntradayIndicators,
        setup: IntradaySetup,
        levels: IntradayLevels | None,
    ) -> Decimal:
        if setup is IntradaySetup.NO_TRIGGER:
            return Decimal("35.00")
        bullish = setup in {
            IntradaySetup.BULLISH_BREAKOUT,
            IntradaySetup.BULLISH_VWAP_RECLAIM,
        }
        score = Decimal("25")
        aligned_vwap = (
            indicators.price_vs_vwap_percent > 0
            if bullish
            else indicators.price_vs_vwap_percent < 0
        )
        aligned_ema = (
            indicators.ema9 > indicators.ema20
            if bullish
            else indicators.ema9 < indicators.ema20
        )
        aligned_momentum = (
            indicators.momentum_5_percent > 0
            if bullish
            else indicators.momentum_5_percent < 0
        )
        score += Decimal("20") if aligned_vwap else ZERO
        score += Decimal("20") if aligned_ema else Decimal("5")
        score += Decimal("15") if aligned_momentum else ZERO
        score += Decimal("15") if indicators.relative_volume >= Decimal("1.2") else Decimal("8")
        direction = "bullish" if bullish else "bearish"
        score += Decimal("5") if indicators.five_minute_bias in {direction, "unavailable"} else ZERO
        if levels is not None and not levels.risk_ok:
            score -= Decimal("20")
        return _bounded_score(score)

    @staticmethod
    def _reasons(
        indicators: IntradayIndicators, setup: IntradaySetup
    ) -> tuple[str, ...]:
        if setup is IntradaySetup.NO_TRIGGER:
            return ("no_confirmed_intraday_trigger",)
        return (
            f"setup:{setup.value}",
            f"vwap_alignment:{indicators.price_vs_vwap_percent}",
            f"relative_volume:{indicators.relative_volume}",
            f"momentum_5m:{indicators.momentum_5_percent}",
            f"ema9_vs_ema20:{rounded(percent_vs(indicators.ema9, indicators.ema20))}",
        )

    @staticmethod
    def _risk_flags(
        indicators: IntradayIndicators,
        setup: IntradaySetup,
        levels: IntradayLevels | None,
    ) -> tuple[str, ...]:
        flags: list[str] = []
        if indicators.relative_volume < Decimal("1.2"):
            flags.append("weak_relative_volume")
        if indicators.atr_percent > Decimal("2"):
            flags.append("elevated_intraday_volatility")
        if levels is not None and not levels.risk_ok:
            flags.append("structural_risk_out_of_bounds")
        if setup is IntradaySetup.NO_TRIGGER:
            flags.append("no_trigger")
        return tuple(flags)

    @staticmethod
    def _direction(setup: IntradaySetup) -> PatternDirection:
        if setup in {
            IntradaySetup.BULLISH_BREAKOUT,
            IntradaySetup.BULLISH_VWAP_RECLAIM,
        }:
            return PatternDirection.BULLISH
        if setup in {
            IntradaySetup.BEARISH_BREAKDOWN,
            IntradaySetup.BEARISH_VWAP_REJECTION,
        }:
            return PatternDirection.BEARISH
        return PatternDirection.NEUTRAL

    @staticmethod
    def _verdict(detail: IntradayAnalysis) -> AnalysisVerdict:
        if detail.setup is IntradaySetup.INSUFFICIENT_DATA:
            return AnalysisVerdict.INSUFFICIENT_DATA
        if detail.setup is IntradaySetup.NO_TRIGGER:
            return AnalysisVerdict.WATCH
        if detail.levels is not None and not detail.levels.risk_ok:
            return AnalysisVerdict.CAUTION
        return AnalysisVerdict.FAVORABLE if detail.score >= Decimal("70") else AnalysisVerdict.WATCH

    @staticmethod
    def _metrics(detail: IntradayAnalysis) -> tuple[NamedValue, ...]:
        values = [NamedValue(name="setup", value=detail.setup.value)]
        if detail.indicators is not None:
            indicators = detail.indicators
            values.extend(
                (
                    NamedValue(name="reference_price", value=indicators.price),
                    NamedValue(name="session_vwap", value=indicators.session_vwap),
                    NamedValue(name="relative_volume", value=indicators.relative_volume),
                    NamedValue(name="ema9", value=indicators.ema9),
                    NamedValue(name="ema20", value=indicators.ema20),
                    NamedValue(name="momentum_5_percent", value=indicators.momentum_5_percent),
                    NamedValue(name="atr14", value=indicators.atr14),
                    NamedValue(name="atr_percent", value=indicators.atr_percent),
                    NamedValue(name="prior_range_high", value=indicators.prior_range_high),
                    NamedValue(name="prior_range_low", value=indicators.prior_range_low),
                    NamedValue(name="five_minute_bias", value=indicators.five_minute_bias),
                    NamedValue(name="risk_flags", value=detail.risk_flags),
                )
            )
        if detail.levels is not None:
            levels = detail.levels
            values.extend(
                (
                    NamedValue(name="invalidation_level", value=levels.invalidation_level),
                    NamedValue(name="objective_level", value=levels.objective_level),
                    NamedValue(name="risk_percent", value=levels.risk_percent),
                    NamedValue(name="reward_risk_ratio", value=levels.reward_risk_ratio),
                    NamedValue(name="risk_ok", value=levels.risk_ok),
                )
            )
        return tuple(values)


def _bounded_score(value: Decimal) -> Decimal:
    return min(HUNDRED, max(ZERO, value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _stable_analysis_id(as_of: datetime, context_digest: str) -> UUID:
    timestamp_ms = int(as_of.timestamp() * 1_000) & ((1 << 48) - 1)
    random_bits = int.from_bytes(hashlib.sha256(context_digest.encode()).digest(), "big") & (
        (1 << 74) - 1
    )
    value = timestamp_ms << 80
    value |= 0x7 << 76
    value |= ((random_bits >> 62) & 0xFFF) << 64
    value |= 0b10 << 62
    value |= random_bits & ((1 << 62) - 1)
    return UUID(int=value)
