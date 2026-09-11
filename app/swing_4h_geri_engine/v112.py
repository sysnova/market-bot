"""Clean N1 support, first crossing candle N3, and N2 recovery confirmation."""

import hashlib
from datetime import datetime
from decimal import Decimal

from app.contracts import (
    GeriAssessment,
    GeriLevelKind,
    GeriMaturity,
    GeriStructuralLevel,
    MarketBar,
    NamedValue,
    TradeSide,
)

from .engine import _validate_bars  # pyright: ignore[reportPrivateUsage]
from .models import Swing4HGeriContext
from .v111 import Swing4HGeriEngineV111


class Swing4HGeriEngineV112(Swing4HGeriEngineV111):
    engine_version = "1.12.0"

    def _structural_assessment(self, context: Swing4HGeriContext) -> GeriAssessment:
        bars = context.bars[-self._lookback :]
        _validate_bars(context.symbol, bars, minimum_bars=self._minimum_bars)
        levels: list[GeriStructuralLevel]
        prior = context.active_structure
        invalid_at: datetime | None = None
        if prior is not None and prior.engine_version == self.engine_version:
            levels = list(prior.levels)
            pending = tuple(b for b in bars if b.timestamp > prior.occurred_at)
            invalid = prior.maturity is GeriMaturity.INVALIDATED
            metrics = {m.name: m.value for m in prior.metrics}
            peak_price = Decimal(str(metrics["clean_support_peak_price"]))
            peak_at = datetime.fromisoformat(str(metrics["clean_support_peak_at"]))
            if metrics.get("clean_support_invalidated_at") is not None:
                invalid_at = datetime.fromisoformat(str(metrics["clean_support_invalidated_at"]))
        else:
            seed = self._clean_seed(bars)
            source = bars[seed]
            levels = [
                GeriStructuralLevel(
                    sequence=1,
                    kind=GeriLevelKind.SUPPORT,
                    price=source.low,
                    source_at=source.timestamp,
                    confirmed_at=bars[seed + self._pivot_radius].timestamp,
                ),
            ]
            pending = bars[seed + self._pivot_radius + 1 :]
            peak = max(bars[seed : seed + self._pivot_radius + 1], key=lambda b: b.high)
            peak_price, peak_at = peak.high, peak.timestamp
            invalid = False

        for bar in pending:
            if invalid:
                index = bars.index(bar) - self._pivot_radius
                if (
                    index < self._pivot_radius
                    or invalid_at is None
                    or bars[index].timestamp < invalid_at
                    or not self._is_clean_seed(bars, index)
                ):
                    continue
                source = bars[index]
                levels = [
                    GeriStructuralLevel(
                        sequence=1,
                        kind=GeriLevelKind.SUPPORT,
                        price=source.low,
                        source_at=source.timestamp,
                        confirmed_at=bar.timestamp,
                    )
                ]
                peak = max(bars[index : index + self._pivot_radius + 1], key=lambda b: b.high)
                peak_price, peak_at = peak.high, peak.timestamp
                invalid, invalid_at = False, None
                continue
            if len(levels) == 1:
                if bar.high > peak_price:
                    peak_price, peak_at = bar.high, bar.timestamp
                n1 = levels[0]
                if bar.low < n1.price:
                    levels = [
                        n1.model_copy(update={"broken_at": bar.timestamp}),
                        GeriStructuralLevel(
                            sequence=2,
                            kind=GeriLevelKind.RESISTANCE,
                            price=peak_price,
                            source_at=peak_at,
                            confirmed_at=bar.timestamp,
                        ),
                        GeriStructuralLevel(
                            sequence=3,
                            kind=GeriLevelKind.SUPPORT,
                            price=bar.low,
                            source_at=bar.timestamp,
                            confirmed_at=bar.timestamp,
                        ),
                    ]
            else:
                n1, n2, n3 = levels[0], levels[1], levels[2]
                if bar.low < n3.price:
                    invalid = True
                    invalid_at = bar.timestamp
                elif n2.broken_at is None and bar.close > n2.price:
                    levels = [n1, n2.model_copy(update={"broken_at": bar.timestamp}), n3]

        active = levels[-1]
        atr = self._atr(bars)
        confirmed = len(levels) == 3 and levels[1].broken_at is not None
        floor = active.price if len(levels) == 3 else None
        ceiling = levels[1].price if len(levels) == 3 else None
        if floor is not None and context.current_price < floor:
            invalid = True
            invalid_at = invalid_at or bars[-1].timestamp
        maturity = (
            GeriMaturity.BUILDING
            if floor is None
            else GeriMaturity.INVALIDATED
            if invalid
            else GeriMaturity.L3
            if confirmed
            else GeriMaturity.IN_ZONE_4H
            if ceiling is not None and context.current_price <= ceiling
            else GeriMaturity.ARMED
        )
        state = (
            "clean_support_invalidated"
            if invalid
            else "n2_break_confirms_swing"
            if confirmed
            else "n3_fixed_waiting_n2_break"
            if floor is not None
            else "clean_n1_waiting_first_cross"
        )
        digest = hashlib.sha256(
            (
                self.engine_version
                + "|"
                + state
                + "|"
                + str(context.current_price)
                + "|".join(v.model_dump_json() for v in levels)
                + "|".join(b.model_dump_json() for b in bars)
            ).encode()
        ).hexdigest()
        return GeriAssessment(
            symbol=context.symbol,
            occurred_at=bars[-1].timestamp,
            engine_version=self.engine_version,
            structure_policy="clean_support_swing",
            maturity=maturity,
            current_price=context.current_price,
            levels=tuple(levels),
            active_level_sequence=active.sequence,
            active_level_kind=active.kind,
            active_level_price=active.price,
            atr14=atr,
            breakout_buffer=Decimal("0"),
            zone_low=floor,
            zone_high=ceiling,
            invalidation=floor,
            bounce_confirmed=confirmed,
            four_hour_confirmation=confirmed,
            standalone_swing=True,
            trade_side=TradeSide.LONG,
            reasons=(state,),
            context_hash=f"sha256:{digest}",
            metrics=(
                NamedValue(name="structure", value="clean_support_swing"),
                NamedValue(name="zone_policy", value="N3_TO_N2"),
                NamedValue(name="break_confirmation", value="completed_4h_close_above_n2"),
                NamedValue(name="n1_break_policy", value="first_low_below_support"),
                NamedValue(name="n3_policy", value="fixed_first_cross_low"),
                NamedValue(name="clean_support_peak_price", value=peak_price),
                NamedValue(name="clean_support_peak_at", value=peak_at.isoformat()),
                NamedValue(
                    name="clean_support_invalidated_at",
                    value=invalid_at.isoformat() if invalid_at else None,
                ),
                NamedValue(name="structural_invalidation_level", value=floor),
                NamedValue(name="emits_opportunities", value=False),
                NamedValue(name="places_orders", value=False),
            ),
        )

    def _clean_seed(self, bars: tuple[MarketBar, ...]) -> int:
        radius = self._pivot_radius
        for i in range(radius, len(bars) - radius):
            if self._is_clean_seed(bars, i):
                return i
        raise ValueError("4HGERI has no confirmed clean support with growth")

    def _is_clean_seed(self, bars: tuple[MarketBar, ...], index: int) -> bool:
        radius = self._pivot_radius
        source = bars[index]
        left = bars[index - radius : index]
        right = bars[index + 1 : index + radius + 1]
        return (
            len(left) == radius
            and len(right) == radius
            and all(b.low >= source.low for b in (*left, *right))
            and any(b.low > source.low for b in left)
            and any(b.high > source.high for b in right)
        )
