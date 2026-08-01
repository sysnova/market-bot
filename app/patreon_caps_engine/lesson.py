"""Patreon lesson consolidation: MA trend, triangle, and Wave 1/2 structure."""

from __future__ import annotations

from decimal import Decimal
from itertools import pairwise

from app.contracts import MarketBar, NamedValue

from .indicators import (
    confirmed_impulse_indices,
    confirmed_pivot_indices,
    mean,
    relative_volume,
    rounded,
    sma,
)
from .models import LessonAssessment, PatreonCapsPolicy

ZERO = Decimal()


def evaluate_lesson(
    daily: tuple[MarketBar, ...],
    hourly: tuple[MarketBar, ...],
    *,
    atr14: Decimal,
    policy: PatreonCapsPolicy,
) -> LessonAssessment:
    if not policy.lesson_enabled:
        return LessonAssessment(
            enabled=False,
            score=ZERO,
            gate_passed=True,
            reasons=("patreon_lesson_v1_disabled",),
        )
    if len(daily) < 205 or len(hourly) < 205:
        return LessonAssessment(
            enabled=True,
            score=ZERO,
            gate_passed=False,
            reasons=("patreon_lesson_history_missing",),
        )

    daily_values = _ma_values(daily)
    hourly_values = _ma_values(hourly)
    cross = _latest_cross(
        tuple(bar.close for bar in daily), policy.cross_lookback_bars
    )
    golden_cross = cross == "golden"
    death_cross = cross == "death"
    ma_score = ZERO
    reasons: list[str] = []

    if daily_values["close"] > daily_values["sma200"]:
        ma_score += Decimal("15")
        reasons.append("daily_above_sma200")
    else:
        reasons.append("daily_below_sma200")
    if daily_values["close"] > daily_values["sma50"]:
        ma_score += Decimal("8")
    if daily_values["sma50_slope5"] > ZERO:
        ma_score += Decimal("6")
    if daily_values["sma200_slope5"] > ZERO:
        ma_score += Decimal("4")
    if daily_values["sma50"] > daily_values["sma200"]:
        ma_score += Decimal("8")
    if golden_cross:
        ma_score += Decimal("9")
        reasons.append("daily_golden_cross")
    if death_cross:
        reasons.append("daily_death_cross")

    if hourly_values["close"] > hourly_values["sma200"]:
        ma_score += Decimal("4")
        reasons.append("hourly_above_sma200")
    if hourly_values["close"] > hourly_values["sma50"]:
        ma_score += Decimal("2")
    if hourly_values["sma50_slope5"] > ZERO:
        ma_score += Decimal("2")
    if hourly_values["sma50"] > hourly_values["sma200"]:
        ma_score += Decimal("2")

    triangle, breakout, retest, resistance = _ascending_triangle(
        daily,
        atr14=atr14,
        lookback=policy.triangle_lookback_bars,
        tolerance_atr=policy.triangle_tolerance_atr,
    )
    wave_hold, wave_retest, fib0618, wave1_high = _wave_structure(
        daily,
        atr14=atr14,
        tolerance_atr=policy.wave_0618_tolerance_atr,
    )
    pattern_score = ZERO
    if triangle:
        pattern_score += Decimal("15")
        reasons.append("ascending_triangle")
    if breakout:
        pattern_score += Decimal("5")
        reasons.append("ascending_triangle_breakout")
    if retest:
        pattern_score += Decimal("5")
        reasons.append("ascending_triangle_retest")
    if wave_hold:
        pattern_score += Decimal("10")
        reasons.append("wave2_holds_fib_0618")
    if wave_retest:
        pattern_score += Decimal("5")
        reasons.append("wave1_high_retest")

    gate_passed = not death_cross and (
        not policy.require_daily_above_sma200
        or daily_values["close"] >= daily_values["sma200"]
    )
    metrics = tuple(
        NamedValue(name=name, value=rounded(value))
        for name, value in (
            ("lesson_daily_sma50", daily_values["sma50"]),
            ("lesson_daily_sma200", daily_values["sma200"]),
            ("lesson_daily_sma50_slope5", daily_values["sma50_slope5"]),
            ("lesson_daily_sma200_slope5", daily_values["sma200_slope5"]),
            ("lesson_hourly_sma50", hourly_values["sma50"]),
            ("lesson_hourly_sma200", hourly_values["sma200"]),
            ("lesson_triangle_resistance", resistance),
            ("lesson_wave_fib_0618", fib0618),
            ("lesson_wave1_high", wave1_high),
        )
        if value > ZERO
    )
    return LessonAssessment(
        enabled=True,
        score=min(Decimal("100"), ma_score + pattern_score),
        gate_passed=gate_passed,
        golden_cross=golden_cross,
        death_cross=death_cross,
        ascending_triangle=triangle,
        triangle_breakout=breakout,
        triangle_retest=retest,
        wave2_0618_hold=wave_hold,
        wave1_high_retest=wave_retest,
        reasons=tuple(reasons) or ("lesson_conditions_absent",),
        metrics=metrics,
    )


def _ma_values(bars: tuple[MarketBar, ...]) -> dict[str, Decimal]:
    closes = tuple(bar.close for bar in bars)
    return {
        "close": closes[-1],
        "sma50": sma(closes, 50),
        "sma200": sma(closes, 200),
        "sma50_slope5": sma(closes, 50) - sma(closes[:-5], 50),
        "sma200_slope5": sma(closes, 200) - sma(closes[:-5], 200),
    }


def _latest_cross(closes: tuple[Decimal, ...], lookback: int) -> str | None:
    start = max(200, len(closes) - lookback)
    for end in range(len(closes), start, -1):
        previous50 = sma(closes[: end - 1], 50)
        previous200 = sma(closes[: end - 1], 200)
        current50 = sma(closes[:end], 50)
        current200 = sma(closes[:end], 200)
        if previous50 <= previous200 and current50 > current200:
            return "golden"
        if previous50 >= previous200 and current50 < current200:
            return "death"
    return None


def _ascending_triangle(
    bars: tuple[MarketBar, ...],
    *,
    atr14: Decimal,
    lookback: int,
    tolerance_atr: Decimal,
) -> tuple[bool, bool, bool, Decimal]:
    selected = bars[-lookback:]
    lows, highs = confirmed_pivot_indices(selected)
    if len(lows) < 2 or len(highs) < 2:
        return False, False, False, ZERO
    high_indices = highs[-3:]
    low_indices = lows[-3:]
    resistance = mean(tuple(selected[index].high for index in high_indices))
    flat_highs = all(
        abs(selected[index].high - resistance) <= atr14 * tolerance_atr
        for index in high_indices
    )
    rising_lows = all(
        selected[right].low > selected[left].low
        for left, right in pairwise(low_indices)
    )
    triangle = flat_highs and rising_lows
    if not triangle:
        return False, False, False, resistance
    last_pivot = max((*high_indices, *low_indices))
    breakout_index = next(
        (
            index
            for index in range(last_pivot + 1, len(selected))
            if selected[index].close > resistance + atr14 * Decimal("0.10")
            and relative_volume(selected[: index + 1], 20) >= Decimal("1.2")
        ),
        None,
    )
    if breakout_index is None:
        return True, False, False, resistance
    retest = any(
        abs(bar.low - resistance) <= atr14 * Decimal("0.25")
        and bar.close >= resistance
        for bar in selected[breakout_index + 1 :]
    )
    return True, True, retest, resistance


def _wave_structure(
    bars: tuple[MarketBar, ...],
    *,
    atr14: Decimal,
    tolerance_atr: Decimal,
) -> tuple[bool, bool, Decimal, Decimal]:
    impulse = confirmed_impulse_indices(bars, atr14=atr14)
    if impulse is None:
        return False, False, ZERO, ZERO
    low_index, high_index = impulse
    if high_index >= len(bars) - 2:
        return False, False, ZERO, bars[high_index].high
    origin = bars[low_index].low
    wave1_high = bars[high_index].high
    impulse_range = wave1_high - origin
    fib0618 = rounded(wave1_high - impulse_range * Decimal("0.618"))
    fib0786 = rounded(wave1_high - impulse_range * Decimal("0.786"))
    pullback = bars[high_index + 1 :]
    pullback_low = min(bar.low for bar in pullback)
    hold = (
        abs(pullback_low - fib0618) <= atr14 * tolerance_atr
        and pullback_low >= fib0786
        and bars[-1].close >= fib0618
    )
    retest = hold and (
        abs(bars[-1].close - wave1_high) <= atr14 * Decimal("0.25")
        or (
            bars[-1].high >= wave1_high
            and bars[-1].close >= wave1_high - atr14 * Decimal("0.10")
        )
    )
    return hold, retest, fib0618, wave1_high
