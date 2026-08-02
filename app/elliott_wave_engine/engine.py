"""Deterministic, no-look-ahead Elliott Wave hypothesis engine."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from itertools import pairwise

from app.contracts import BarTimeframe, MarketBar, NamedValue, WaveAssessment, WavePhase

from .models import WaveContext

ZERO = Decimal()
FOUR_PLACES = Decimal("0.0001")


@dataclass(frozen=True, slots=True)
class _Candidate:
    phase: WavePhase
    score: Decimal
    origin: Decimal
    wave1_peak: Decimal
    wave2_low: Decimal | None
    wave3_peak: Decimal | None
    correction_low: Decimal
    retracement: Decimal
    entry_low: Decimal
    entry_high: Decimal
    trigger: Decimal
    invalidation: Decimal
    target_low: Decimal
    target_high: Decimal
    reasons: tuple[str, ...]
    violations: tuple[str, ...] = ()


class ElliottWaveEngine:
    """Rank Wave 2 and Wave 4 hypotheses without relabelling upstream analyses."""

    engine_id = "elliott-wave"
    engine_version = "0.1.0"

    def evaluate(self, context: WaveContext) -> WaveAssessment:
        bars = tuple(bar for bar in context.daily_bars if bar.is_final)
        if len(bars) < 15:
            return self._unresolved(context.symbol, bars, "insufficient_daily_history")
        atr14 = _atr(bars)
        lows, highs = _confirmed_pivots(bars)
        candidates = tuple(
            sorted(
                (
                    *_wave4_candidates(bars, lows, highs, atr14),
                    *_wave2_candidates(bars, lows, highs, atr14),
                ),
                key=lambda item: item.score,
                reverse=True,
            )
        )
        if not candidates:
            return self._unresolved(context.symbol, bars, "no_valid_impulse_count")
        primary = candidates[0]
        alternative = next((item for item in candidates[1:] if item.phase != primary.phase), None)
        hourly_confirmation = _hourly_reversal(context.hourly_bars)
        score = min(
            Decimal("100"),
            primary.score + (Decimal("5") if hourly_confirmation else ZERO),
        )
        reasons = (
            (*primary.reasons, "hourly_reversal_confirmation")
            if hourly_confirmation
            else primary.reasons
        )
        return WaveAssessment(
            symbol=context.symbol.strip().upper(),
            occurred_at=bars[-1].timestamp,
            engine_version=self.engine_version,
            primary_timeframe=BarTimeframe.DAY_1,
            phase=primary.phase,
            score=_rounded(score),
            confidence=_rounded(score / Decimal("100")),
            current_price=bars[-1].close,
            wave1_origin=primary.origin,
            wave1_peak=primary.wave1_peak,
            wave2_low=primary.wave2_low,
            wave3_peak=primary.wave3_peak,
            corrective_low=primary.correction_low,
            retracement=_rounded(primary.retracement),
            entry_zone_low=primary.entry_low,
            entry_zone_high=primary.entry_high,
            trigger_price=primary.trigger,
            invalidation=primary.invalidation,
            target_low=primary.target_low,
            target_high=primary.target_high,
            alternative_phase=alternative.phase if alternative is not None else None,
            alternative_score=_rounded(alternative.score) if alternative is not None else None,
            reasons=reasons,
            violations=primary.violations,
            metrics=(NamedValue(name="atr14_daily", value=_rounded(atr14)),),
            context_hash=_context_hash(bars),
        )

    def _unresolved(
        self, symbol: str, bars: tuple[MarketBar, ...], reason: str
    ) -> WaveAssessment:
        if not bars:
            raise ValueError("Elliott Wave analysis requires at least one completed daily bar")
        return WaveAssessment(
            symbol=symbol.strip().upper(),
            occurred_at=bars[-1].timestamp,
            engine_version=self.engine_version,
            primary_timeframe=BarTimeframe.DAY_1,
            phase=WavePhase.UNRESOLVED,
            score=Decimal("15"),
            confidence=Decimal("0.15"),
            current_price=bars[-1].close,
            reasons=(reason,),
            context_hash=_context_hash(bars),
        )


def _wave2_candidates(
    bars: tuple[MarketBar, ...],
    lows: tuple[int, ...],
    highs: tuple[int, ...],
    atr14: Decimal,
) -> tuple[_Candidate, ...]:
    output: list[_Candidate] = []
    for peak_index in reversed(highs):
        origins = tuple(index for index in lows if index <= peak_index - 3)
        if not origins or peak_index >= len(bars) - 2:
            continue
        origin_index = origins[-1]
        origin = bars[origin_index].low
        peak = bars[peak_index].high
        impulse = peak - origin
        if impulse < atr14 * Decimal("2"):
            continue
        correction_slice = bars[peak_index + 1 :]
        correction_index = min(
            range(peak_index + 1, len(bars)), key=lambda index: bars[index].low
        )
        correction_low = bars[correction_index].low
        retracement = (peak - correction_low) / impulse
        if correction_low <= origin or not Decimal("0.45") <= retracement <= Decimal("0.86"):
            continue
        entry_low = peak - impulse * Decimal("0.786")
        entry_high = peak - impulse * Decimal("0.500")
        recovery = (bars[-1].close - correction_low) / impulse
        score = Decimal("45")
        reasons = ["wave1_impulse_confirmed", "wave2_retracement_in_0500_0786"]
        if Decimal("0.50") <= retracement <= Decimal("0.786"):
            score += Decimal("20")
        if recovery >= Decimal("0.15"):
            score += Decimal("15")
            reasons.append("correction_recovery_confirmed")
        if bars[-1].close >= bars[-2].close:
            score += Decimal("10")
            reasons.append("daily_reversal_follow_through")
        if bars[-1].volume >= _mean_volume(correction_slice):
            score += Decimal("5")
            reasons.append("volume_supports_recovery")
        target_low = correction_low + impulse * Decimal("1.618")
        target_high = correction_low + impulse * Decimal("2.000")
        if bars[-1].close > target_high:
            continue
        phase = WavePhase.WAVE_3_ACTIVE if bars[-1].close > peak else WavePhase.WAVE_2_ENDING
        trigger = _trigger_price(bars, correction_index)
        output.append(
            _Candidate(
                phase=phase,
                score=score,
                origin=_rounded(origin),
                wave1_peak=_rounded(peak),
                wave2_low=_rounded(correction_low),
                wave3_peak=None,
                correction_low=_rounded(correction_low),
                retracement=retracement,
                entry_low=_rounded(entry_low),
                entry_high=_rounded(entry_high),
                trigger=_rounded(trigger),
                invalidation=_rounded(origin),
                target_low=_rounded(target_low),
                target_high=_rounded(target_high),
                reasons=tuple(reasons),
            )
        )
    return tuple(output)


def _wave4_candidates(
    bars: tuple[MarketBar, ...],
    lows: tuple[int, ...],
    highs: tuple[int, ...],
    atr14: Decimal,
) -> tuple[_Candidate, ...]:
    output: list[_Candidate] = []
    for wave3_index in reversed(highs):
        wave2_indices = tuple(index for index in lows if index <= wave3_index - 3)
        if not wave2_indices or wave3_index >= len(bars) - 2:
            continue
        wave2_index = wave2_indices[-1]
        wave1_indices = tuple(index for index in highs if index <= wave2_index - 3)
        if not wave1_indices:
            continue
        wave1_index = wave1_indices[-1]
        origin_indices = tuple(index for index in lows if index <= wave1_index - 3)
        if not origin_indices:
            continue
        origin_index = origin_indices[-1]
        origin = bars[origin_index].low
        wave1_peak = bars[wave1_index].high
        wave2_low = bars[wave2_index].low
        wave3_peak = bars[wave3_index].high
        wave1_size = wave1_peak - origin
        wave3_size = wave3_peak - wave2_low
        if wave1_size < atr14 * Decimal("1.5") or wave3_size < wave1_size:
            continue
        wave2_retracement = (wave1_peak - wave2_low) / wave1_size
        if not Decimal("0.45") <= wave2_retracement <= Decimal("0.86"):
            continue
        correction_index = min(
            range(wave3_index + 1, len(bars)), key=lambda index: bars[index].low
        )
        correction_low = bars[correction_index].low
        retracement = (wave3_peak - correction_low) / wave3_size
        if not Decimal("0.20") <= retracement <= Decimal("0.55"):
            continue
        violations: list[str] = []
        if correction_low <= wave1_peak:
            if correction_low >= wave1_peak - atr14 * Decimal("0.15"):
                violations.append("minor_wave4_wave1_overlap")
            else:
                continue
        # A complete 1-2-3 structure is more informative than the older Wave 2
        # hypothesis that necessarily remains visible inside it.
        score = Decimal("60")
        reasons = ["waves_1_2_3_confirmed", "wave4_retracement_in_0236_0500"]
        if Decimal("0.236") <= retracement <= Decimal("0.500"):
            score += Decimal("20")
        if Decimal("0.30") <= retracement <= Decimal("0.45"):
            score += Decimal("10")
            reasons.append("wave4_near_0382")
        if bars[-1].close >= bars[-2].close:
            score += Decimal("10")
            reasons.append("daily_reversal_follow_through")
        if violations:
            score -= Decimal("15")
        target_low = origin + wave1_size * Decimal("2.000")
        target_high = origin + wave1_size * Decimal("2.618")
        if bars[-1].close > target_high:
            continue
        phase = WavePhase.WAVE_5_ACTIVE if bars[-1].close > wave3_peak else WavePhase.WAVE_4_ENDING
        entry_low = wave3_peak - wave3_size * Decimal("0.500")
        entry_high = wave3_peak - wave3_size * Decimal("0.236")
        trigger = _trigger_price(bars, correction_index)
        output.append(
            _Candidate(
                phase=phase,
                score=score,
                origin=_rounded(origin),
                wave1_peak=_rounded(wave1_peak),
                wave2_low=_rounded(wave2_low),
                wave3_peak=_rounded(wave3_peak),
                correction_low=_rounded(correction_low),
                retracement=retracement,
                entry_low=_rounded(entry_low),
                entry_high=_rounded(entry_high),
                trigger=_rounded(trigger),
                invalidation=_rounded(max(origin, wave1_peak - atr14 * Decimal("0.15"))),
                target_low=_rounded(target_low),
                target_high=_rounded(target_high),
                reasons=tuple(reasons),
                violations=tuple(violations),
            )
        )
    return tuple(output)


def _confirmed_pivots(
    bars: tuple[MarketBar, ...], radius: int = 2
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    lows: list[int] = []
    highs: list[int] = []
    for index in range(radius, len(bars) - radius):
        neighbors = (*bars[index - radius : index], *bars[index + 1 : index + radius + 1])
        if all(bars[index].low <= bar.low for bar in neighbors) and any(
            bars[index].low < bar.low for bar in neighbors
        ):
            lows.append(index)
        if all(bars[index].high >= bar.high for bar in neighbors) and any(
            bars[index].high > bar.high for bar in neighbors
        ):
            highs.append(index)
    return tuple(lows), tuple(highs)


def _atr(bars: tuple[MarketBar, ...], period: int = 14) -> Decimal:
    selected = bars[-(period + 1) :]
    ranges = tuple(
        max(
            current.high - current.low,
            abs(current.high - previous.close),
            abs(current.low - previous.close),
        )
        for previous, current in pairwise(selected)
    )
    return sum(ranges, ZERO) / Decimal(len(ranges))


def _mean_volume(bars: tuple[MarketBar, ...]) -> Decimal:
    return sum((bar.volume for bar in bars), ZERO) / Decimal(len(bars))


def _hourly_reversal(bars: tuple[MarketBar, ...]) -> bool:
    completed = tuple(bar for bar in bars if bar.is_final)
    return len(completed) >= 3 and completed[-1].close > completed[-2].close


def _trigger_price(bars: tuple[MarketBar, ...], correction_index: int) -> Decimal:
    prior = bars[max(correction_index, len(bars) - 5) : -1]
    return max((bar.high for bar in prior), default=bars[-1].high)


def _rounded(value: Decimal) -> Decimal:
    return value.quantize(FOUR_PLACES, rounding=ROUND_HALF_UP)


def _context_hash(bars: tuple[MarketBar, ...]) -> str:
    payload = tuple(
        (
            bar.timestamp.isoformat(),
            str(bar.open),
            str(bar.high),
            str(bar.low),
            str(bar.close),
            str(bar.volume),
        )
        for bar in bars
    )
    digest = hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()
    return f"sha256:{digest}"
