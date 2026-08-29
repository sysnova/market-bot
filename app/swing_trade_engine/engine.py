"""Causal LONG Fibonacci SwingTrade zone engine."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from itertools import pairwise
from zoneinfo import ZoneInfo

from app.contracts import (
    BarTimeframe,
    GeriLevelKind,
    GeriMaturity,
    MarketBar,
    NamedValue,
    SwingTradeMaturity,
    TradeSide,
)
from app.contracts.geri_4h import GeriAssessment
from app.contracts.swing_trade import SwingTradeAssessment

from .models import SwingTradeContext

ZERO = Decimal("0")
FOUR = Decimal("0.0001")
_NEW_YORK = ZoneInfo("America/New_York")


class SwingTradeEngine:
    """Evaluate one completed-daily impulse at each completed 15-minute spot."""

    engine_id = "swing-trade"
    engine_version = "1.0.0"

    def __init__(
        self,
        *,
        fibonacci_lookback_sessions: int = 60,
        movement_lookback_sessions: int = 20,
        fibonacci_50_ratio: Decimal = Decimal("0.500"),
        fibonacci_618_ratio: Decimal = Decimal("0.618"),
        fibonacci_1618_ratio: Decimal = Decimal("1.618"),
        support_band_atr: Decimal = Decimal("0.25"),
        invalidation_atr: Decimal = Decimal("0.50"),
        minimum_reward_risk: Decimal = Decimal("1.50"),
        maximum_distance_to_zone_atr: Decimal = Decimal("3"),
        geri_freshness_sessions: int = 2,
        tracking_ttl_sessions: int = 10,
        trade_ttl_sessions: int = 10,
        strategy_version: str = "1.0.0",
    ) -> None:
        if fibonacci_lookback_sessions < 20:
            raise ValueError("Fibonacci lookback must be at least 20 sessions")
        if (
            movement_lookback_sessions < 2
            or movement_lookback_sessions > fibonacci_lookback_sessions
        ):
            raise ValueError("movement lookback must fit inside Fibonacci lookback")
        if not ZERO < fibonacci_50_ratio < fibonacci_618_ratio < Decimal("1"):
            raise ValueError("Fibonacci retracement ratios are out of order")
        if fibonacci_1618_ratio <= Decimal("1"):
            raise ValueError("Fibonacci extension ratio must exceed one")
        decimals = {
            "support_band_atr": support_band_atr,
            "invalidation_atr": invalidation_atr,
            "minimum_reward_risk": minimum_reward_risk,
            "maximum_distance_to_zone_atr": maximum_distance_to_zone_atr,
        }
        if any(value <= ZERO for value in decimals.values()):
            raise ValueError("SwingTrade ATR and R/R parameters must be positive")
        if geri_freshness_sessions < 1 or tracking_ttl_sessions < 1 or trade_ttl_sessions < 1:
            raise ValueError("SwingTrade session parameters must be positive")
        self._fib_lookback = fibonacci_lookback_sessions
        self._movement_lookback = movement_lookback_sessions
        self._fib50_ratio = fibonacci_50_ratio
        self._fib618_ratio = fibonacci_618_ratio
        self._fib1618_ratio = fibonacci_1618_ratio
        self._support_band_atr = support_band_atr
        self._invalidation_atr = invalidation_atr
        self._minimum_rr = minimum_reward_risk
        self._maximum_distance_atr = maximum_distance_to_zone_atr
        self._geri_freshness = geri_freshness_sessions
        self._tracking_ttl = tracking_ttl_sessions
        self._trade_ttl = trade_ttl_sessions
        self._strategy_version = strategy_version

    def analyze(self, context: SwingTradeContext) -> SwingTradeAssessment:
        """Evaluate the operational thesis using its immutable extrema rule."""

        return self._analyze(context, causal_geometry=False)

    def analyze_geometry(self, context: SwingTradeContext) -> SwingTradeAssessment:
        """Calculate display geometry from the widest causal low-to-later-high pair."""

        return self._analyze(context, causal_geometry=True)

    def _analyze(
        self,
        context: SwingTradeContext,
        *,
        causal_geometry: bool,
    ) -> SwingTradeAssessment:
        symbol = context.symbol.strip().upper()
        bars = tuple(bar for bar in context.daily_bars if bar.is_final)[-self._fib_lookback :]
        if len(bars) < self._fib_lookback:
            raise ValueError("SwingTrade requires the configured completed daily window")
        if any(bar.symbol != symbol for bar in bars):
            raise ValueError("SwingTrade bars must belong to the requested symbol")
        if any(bar.timeframe is not BarTimeframe.DAY_1 for bar in bars):
            raise ValueError("SwingTrade requires completed daily bars")
        if any(current.timestamp <= previous.timestamp for previous, current in pairwise(bars)):
            raise ValueError("SwingTrade daily bars must be chronological")
        if context.current_price <= ZERO:
            raise ValueError("SwingTrade spot must be positive")

        if causal_geometry:
            low_bar, high_bar = _widest_causal_long_impulse(bars)
        else:
            low_bar = min(bars, key=lambda bar: (bar.low, bar.timestamp))
            high_bar = max(bars, key=lambda bar: (bar.high, -bar.timestamp.timestamp()))
            if low_bar.timestamp >= high_bar.timestamp:
                raise ValueError("SwingTrade LONG impulse requires low before high")
        impulse_range = high_bar.high - low_bar.low
        if impulse_range <= ZERO:
            raise ValueError("SwingTrade impulse range must be positive")

        atr14 = _atr(bars)
        fib50 = high_bar.high - impulse_range * self._fib50_ratio
        fib618 = high_bar.high - impulse_range * self._fib618_ratio
        fib1618 = low_bar.low + impulse_range * self._fib1618_ratio
        movement = bars[-self._movement_lookback :]
        support20 = min(bar.low for bar in movement)
        resistance20 = min(high_bar.high, max(bar.high for bar in movement))
        band_low = max(FOUR, support20 - atr14 * self._support_band_atr)
        band_high = support20 + atr14 * self._support_band_atr
        invalidation = max(FOUR, support20 - atr14 * self._invalidation_atr)
        risk = context.current_price - invalidation
        primary_reward = resistance20 - context.current_price
        extended_reward = fib1618 - context.current_price
        reward_risk = ZERO if risk <= ZERO else primary_reward / risk
        extended_rr = ZERO if risk <= ZERO else extended_reward / risk
        support_confluence = _overlaps(fib618, fib50, band_low, band_high)
        spot_in_zone = fib618 <= context.current_price <= fib50
        within_distance = context.current_price <= fib50 + atr14 * self._maximum_distance_atr
        base = bool(
            context.current_price >= fib618
            and context.current_price < resistance20
            and context.current_price > invalidation
            and within_distance
            and reward_risk > self._minimum_rr
        )
        geri_valid, geri_confluence = _geri_confluence(
            context,
            bars=bars,
            freshness_sessions=self._geri_freshness,
            zone_low=fib618,
            zone_high=fib50,
        )
        maturity: SwingTradeMaturity | None = None
        if base:
            maturity = SwingTradeMaturity.ST1
            if support_confluence:
                maturity = SwingTradeMaturity.ST2
                if spot_in_zone:
                    maturity = SwingTradeMaturity.ST3
                    if geri_valid and geri_confluence:
                        maturity = SwingTradeMaturity.ST4

        reasons = _reasons(
            maturity=maturity,
            base=base,
            support_confluence=support_confluence,
            spot_in_zone=spot_in_zone,
            geri_valid=geri_valid,
            geri_confluence=geri_confluence,
            reward_risk=reward_risk,
            minimum_rr=self._minimum_rr,
            within_distance=within_distance,
        )
        context_geri = context.geri
        geri = context_geri if geri_valid else None
        return SwingTradeAssessment(
            symbol=symbol,
            occurred_at=context.as_of,
            engine_version=self.engine_version,
            strategy_version=self._strategy_version,
            maturity=maturity,
            current_price=_rounded(context.current_price),
            impulse_low=_rounded(low_bar.low),
            impulse_low_at=low_bar.timestamp,
            impulse_high=_rounded(high_bar.high),
            impulse_high_at=high_bar.timestamp,
            fibonacci_50=_rounded(fib50),
            fibonacci_618=_rounded(fib618),
            fibonacci_1618=_rounded(fib1618),
            zone_low=_rounded(fib618),
            zone_high=_rounded(fib50),
            support_20d=_rounded(support20),
            resistance_20d=_rounded(resistance20),
            support_band_low=_rounded(band_low),
            support_band_high=_rounded(band_high),
            invalidation=_rounded(invalidation),
            primary_target=_rounded(resistance20),
            extended_target=_rounded(fib1618),
            atr14=_rounded(atr14),
            reward_risk=_rounded(reward_risk),
            extended_reward_risk=_rounded(extended_rr),
            support_confluence=support_confluence,
            spot_in_fibonacci_zone=spot_in_zone,
            geri_assessment_id=geri.assessment_id if geri is not None else None,
            geri_zone_low=geri.zone_low if geri is not None else None,
            geri_zone_high=geri.zone_high if geri is not None else None,
            geri_confluence=geri_confluence,
            eligible=maturity is not None,
            reasons=reasons,
            metrics=(
                NamedValue(
                    name="setup_id",
                    value=_setup_id(
                        symbol, low_bar.timestamp, high_bar.timestamp, self._strategy_version
                    ),
                ),
                NamedValue(name="minimum_reward_risk", value=self._minimum_rr),
                NamedValue(name="tracking_ttl_sessions", value=self._tracking_ttl),
                NamedValue(name="trade_ttl_sessions", value=self._trade_ttl),
                NamedValue(name="places_orders", value=False),
            ),
            context_hash=_context_hash(bars, context.current_price, context_geri),
        )


def _widest_causal_long_impulse(
    bars: tuple[MarketBar, ...],
) -> tuple[MarketBar, MarketBar]:
    """Return the widest positive low-to-later-high range without lookahead."""

    if len(bars) < 2:
        raise ValueError("SwingTrade LONG geometry requires at least two daily bars")
    running_low = bars[0]
    best_low: MarketBar | None = None
    best_high: MarketBar | None = None
    best_range = ZERO
    for high_bar in bars[1:]:
        impulse_range = high_bar.high - running_low.low
        if impulse_range > best_range:
            best_low = running_low
            best_high = high_bar
            best_range = impulse_range
        if (high_bar.low, high_bar.timestamp) < (
            running_low.low,
            running_low.timestamp,
        ):
            running_low = high_bar
    if best_low is None or best_high is None:
        raise ValueError("SwingTrade LONG geometry has no positive low-to-later-high range")
    return best_low, best_high


def _geri_confluence(
    context: SwingTradeContext,
    *,
    bars: tuple[MarketBar, ...],
    freshness_sessions: int,
    zone_low: Decimal,
    zone_high: Decimal,
) -> tuple[bool, bool]:
    geri = context.geri
    if (
        geri is None
        or geri.engine_version not in {"1.2.0", "1.3.0", "1.4.0"}
        or not geri.standalone_swing
    ):
        return False, False
    cutoff_index = max(0, len(bars) - freshness_sessions)
    if geri.occurred_at < bars[cutoff_index].timestamp:
        return False, False
    if (
        geri.trade_side is not TradeSide.LONG
        or geri.active_level_kind is not GeriLevelKind.SUPPORT
        or geri.maturity
        in {
            GeriMaturity.BUILDING,
            GeriMaturity.EXTENDED,
            GeriMaturity.RECLAIM_REQUIRED,
            GeriMaturity.INVALIDATED,
        }
        or geri.zone_low is None
        or geri.zone_high is None
    ):
        return False, False
    geri_zone_low = geri.zone_low
    geri_zone_high = geri.zone_high
    spot_inside = geri_zone_low <= context.current_price <= geri_zone_high
    return True, spot_inside and _overlaps(
        zone_low,
        zone_high,
        geri_zone_low,
        geri_zone_high,
    )


def _reasons(
    *,
    maturity: SwingTradeMaturity | None,
    base: bool,
    support_confluence: bool,
    spot_in_zone: bool,
    geri_valid: bool,
    geri_confluence: bool,
    reward_risk: Decimal,
    minimum_rr: Decimal,
    within_distance: bool,
) -> tuple[str, ...]:
    reasons = (
        [f"swing_trade_{maturity.value.lower()}"]
        if maturity is not None
        else ["swing_trade_no_thesis"]
    )
    flags = {
        "support_confluence": support_confluence,
        "spot_in_zone": spot_in_zone,
        "geri_valid": geri_valid,
        "geri_confluence": geri_confluence,
    }
    for name, enabled in flags.items():
        if enabled:
            reasons.append(name)
    if not base:
        reasons.append("base_gate_failed")
    if not within_distance:
        reasons.append("too_far_from_fibonacci_zone")
    if reward_risk <= minimum_rr:
        reasons.append("insufficient_reward_risk")
    return tuple(reasons)


def _overlaps(a_low: Decimal, a_high: Decimal, b_low: Decimal, b_high: Decimal) -> bool:
    return max(a_low, b_low) <= min(a_high, b_high)


def _atr(bars: tuple[MarketBar, ...], period: int = 14) -> Decimal:
    ranges = tuple(
        max(
            current.high - current.low,
            abs(current.high - previous.close),
            abs(current.low - previous.close),
        )
        for previous, current in pairwise(bars)
    )
    sample = ranges[-period:]
    if not sample:
        raise ValueError("SwingTrade ATR requires multiple bars")
    result = sum(sample, ZERO) / Decimal(len(sample))
    if result <= ZERO:
        raise ValueError("SwingTrade ATR must be positive")
    return result


def _setup_id(symbol: str, low_at: datetime, high_at: datetime, version: str) -> str:
    return f"swing-trade:{symbol}:{low_at.isoformat()}:{high_at.isoformat()}:{version}"


def _context_hash(bars: tuple[MarketBar, ...], price: Decimal, geri: GeriAssessment | None) -> str:
    payload = {
        "bars": [
            [bar.timestamp.isoformat(), str(bar.open), str(bar.high), str(bar.low), str(bar.close)]
            for bar in bars
        ],
        "price": str(price),
        "geri": str(geri.assessment_id) if geri is not None else None,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return f"sha256:{digest}"


def _rounded(value: Decimal) -> Decimal:
    return value.quantize(FOUR, rounding=ROUND_HALF_UP)


class SwingTradeEngineV11(SwingTradeEngine):
    """Require a causal 15-minute rejection, normalized volume and VWAP for ST3."""

    engine_version = "1.1.0"

    def __init__(
        self,
        *,
        fibonacci_lookback_sessions: int = 60,
        movement_lookback_sessions: int = 20,
        fibonacci_50_ratio: Decimal = Decimal("0.500"),
        fibonacci_618_ratio: Decimal = Decimal("0.618"),
        fibonacci_1618_ratio: Decimal = Decimal("1.618"),
        support_band_atr: Decimal = Decimal("0.25"),
        invalidation_atr: Decimal = Decimal("0.50"),
        minimum_reward_risk: Decimal = Decimal("1.50"),
        maximum_distance_to_zone_atr: Decimal = Decimal("3"),
        geri_freshness_sessions: int = 2,
        tracking_ttl_sessions: int = 10,
        trade_ttl_sessions: int = 10,
        minimum_intraday_rvol: Decimal = Decimal("1.20"),
        minimum_rvol_samples: int = 5,
        require_vwap_gate: bool = True,
        strategy_version: str = "1.1.0",
    ) -> None:
        super().__init__(
            fibonacci_lookback_sessions=fibonacci_lookback_sessions,
            movement_lookback_sessions=movement_lookback_sessions,
            fibonacci_50_ratio=fibonacci_50_ratio,
            fibonacci_618_ratio=fibonacci_618_ratio,
            fibonacci_1618_ratio=fibonacci_1618_ratio,
            support_band_atr=support_band_atr,
            invalidation_atr=invalidation_atr,
            minimum_reward_risk=minimum_reward_risk,
            maximum_distance_to_zone_atr=maximum_distance_to_zone_atr,
            geri_freshness_sessions=geri_freshness_sessions,
            tracking_ttl_sessions=tracking_ttl_sessions,
            trade_ttl_sessions=trade_ttl_sessions,
            strategy_version=strategy_version,
        )
        if minimum_intraday_rvol <= ZERO:
            raise ValueError("minimum intraday RVOL must be positive")
        if minimum_rvol_samples < 1:
            raise ValueError("minimum RVOL samples must be positive")
        self._minimum_intraday_rvol = minimum_intraday_rvol
        self._minimum_rvol_samples = minimum_rvol_samples
        self._require_vwap_gate = require_vwap_gate

    def analyze(self, context: SwingTradeContext) -> SwingTradeAssessment:
        _validate_v11_context(context)
        result: SwingTradeAssessment = super().analyze(context)
        confirmations = context.confirmation_bars
        rejection = _long_rejection_confirmed(
            confirmations,
            zone_low=result.zone_low,
            zone_high=result.zone_high,
        )
        current = confirmations[-1] if confirmations else None
        vwap_passed = bool(
            not self._require_vwap_gate
            or (
                current is not None
                and current.vwap is not None
                and current.close > current.vwap
            )
        )
        rvol = _session_normalized_rvol(
            confirmations,
            minimum_samples=self._minimum_rvol_samples,
        )
        rvol_passed = rvol is not None and rvol >= self._minimum_intraday_rvol
        trigger = rejection and vwap_passed and rvol_passed
        geri_reaction = bool(
            context.geri is not None
            and context.geri.maturity
            in {GeriMaturity.L2_4H, GeriMaturity.L3, GeriMaturity.L4}
        )

        maturity = result.maturity
        if maturity is SwingTradeMaturity.ST4 and not geri_reaction:
            maturity = SwingTradeMaturity.ST3
        if maturity in {SwingTradeMaturity.ST3, SwingTradeMaturity.ST4} and not trigger:
            maturity = (
                SwingTradeMaturity.ST2
                if result.support_confluence
                else SwingTradeMaturity.ST1
            )

        reasons = list(result.reasons)
        if result.spot_in_fibonacci_zone and not trigger:
            reasons.append("swing_trade_entry_trigger_pending")
        if not rejection:
            reasons.append("entry_rejection_pending")
        if not vwap_passed:
            reasons.append("entry_vwap_gate_pending")
        if not rvol_passed:
            reasons.append("entry_session_rvol_pending")
        if result.geri_confluence and not geri_reaction:
            reasons.append("geri_reaction_confirmation_pending")

        return result.model_copy(
            update={
                "engine_version": self.engine_version,
                "maturity": maturity,
                "eligible": maturity is not None,
                "reasons": tuple(dict.fromkeys(reasons)),
                "metrics": _upsert_metrics(
                    result,
                    NamedValue(name="entry_rejection_confirmed", value=rejection),
                    NamedValue(name="entry_vwap_gate_passed", value=vwap_passed),
                    NamedValue(name="intraday_rvol20_same_slot", value=rvol),
                    NamedValue(name="intraday_rvol_confirmed", value=rvol_passed),
                    NamedValue(
                        name="minimum_intraday_rvol",
                        value=self._minimum_intraday_rvol,
                    ),
                    NamedValue(name="geri_reaction_confirmed", value=geri_reaction),
                    NamedValue(name="swing_trade_entry_trigger_passed", value=trigger),
                ),
            },
        )


def _validate_v11_context(context: SwingTradeContext) -> None:
    if context.as_of.tzinfo is None or context.as_of.utcoffset() != timedelta(0):
        raise ValueError("SwingTrade as_of must be timezone-aware UTC")
    if context.current_price_at is None:
        raise ValueError("SwingTrade v1.1 requires current_price_at")
    if (
        context.current_price_at.tzinfo is None
        or context.current_price_at.utcoffset() != timedelta(0)
    ):
        raise ValueError("SwingTrade current_price_at must be timezone-aware UTC")
    if context.current_price_at > context.as_of:
        raise ValueError("SwingTrade current_price_at is later than as_of")
    symbol = context.symbol.strip().upper()
    evidence = (*context.daily_bars, *context.confirmation_bars)
    if any(bar.timestamp > context.as_of for bar in evidence):
        raise ValueError("SwingTrade evidence is later than as_of")
    if any(bar.symbol != symbol for bar in evidence):
        raise ValueError("SwingTrade evidence must belong to the requested symbol")
    if any(not bar.is_final for bar in evidence):
        raise ValueError("SwingTrade v1.1 requires final evidence")
    if any(bar.timeframe is not BarTimeframe.DAY_1 for bar in context.daily_bars):
        raise ValueError("SwingTrade structural bars must use 1Day")
    if any(bar.timeframe is not BarTimeframe.MINUTE_15 for bar in context.confirmation_bars):
        raise ValueError("SwingTrade confirmation bars must use 15Min")
    if any(
        current.timestamp <= previous.timestamp
        for values in (context.daily_bars, context.confirmation_bars)
        for previous, current in pairwise(values)
    ):
        raise ValueError("SwingTrade evidence must be chronological")
    if any(bar.timestamp > context.current_price_at for bar in context.daily_bars) or any(
        bar.timestamp + timedelta(minutes=15) > context.current_price_at
        for bar in context.confirmation_bars
    ):
        raise ValueError("SwingTrade evidence is later than current_price_at")
    if context.geri is not None:
        if context.geri.symbol != symbol:
            raise ValueError("SwingTrade GERI evidence must belong to the requested symbol")
        if context.geri.occurred_at > context.as_of:
            raise ValueError("SwingTrade GERI evidence is later than as_of")
        if context.geri.occurred_at > context.current_price_at:
            raise ValueError("SwingTrade GERI evidence is later than current_price_at")


def _long_rejection_confirmed(
    bars: tuple[MarketBar, ...],
    *,
    zone_low: Decimal,
    zone_high: Decimal,
) -> bool:
    if len(bars) < 2:
        return False
    touched, current = bars[-2:]
    touched_zone = touched.low <= zone_high and touched.high >= zone_low
    return bool(
        touched_zone
        and current.low > touched.low
        and current.close > current.open
        and current.close > touched.close
        and current.close >= zone_low
    )


def _session_normalized_rvol(
    bars: tuple[MarketBar, ...], *, minimum_samples: int
) -> Decimal | None:
    if not bars:
        return None
    current = bars[-1]
    current_local = current.timestamp.astimezone(_NEW_YORK)
    slot = (current_local.hour, current_local.minute)
    baseline_values = tuple(
        bar.volume
        for bar in bars[:-1]
        if (
            bar.timestamp.astimezone(_NEW_YORK).hour,
            bar.timestamp.astimezone(_NEW_YORK).minute,
        )
        == slot
        and bar.timestamp.astimezone(_NEW_YORK).date() != current_local.date()
    )[-20:]
    if len(baseline_values) < minimum_samples:
        return None
    baseline = sum(baseline_values, ZERO) / Decimal(len(baseline_values))
    if baseline <= ZERO:
        return None
    return _rounded(current.volume / baseline)


def _upsert_metrics(
    result: SwingTradeAssessment, *items: NamedValue
) -> tuple[NamedValue, ...]:
    names = {item.name for item in items}
    return (*(item for item in result.metrics if item.name not in names), *items)
