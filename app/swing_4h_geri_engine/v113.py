"""Most recent valid clean-support swing, with optional Fibonacci confluence."""

from dataclasses import replace
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
from .v112 import Swing4HGeriEngineV112


class Swing4HGeriEngineV113(Swing4HGeriEngineV112):
    engine_version = "1.13.0"

    def _structural_assessment(self, context: Swing4HGeriContext) -> GeriAssessment:
        bars = context.bars[-self._lookback :]
        _validate_bars(context.symbol, bars, minimum_bars=self._minimum_bars)
        candidates: list[GeriAssessment] = []
        prior = context.active_structure
        if prior is not None and prior.engine_version == self.engine_version:
            candidates.append(super()._structural_assessment(context))
        for index in range(self._pivot_radius, len(bars) - self._pivot_radius):
            if not self._is_clean_seed(bars, index):
                continue
            seed = self._candidate_seed(context, bars, index)
            candidates.append(
                super()._structural_assessment(replace(context, active_structure=seed))
            )
        if not candidates:
            raise ValueError("4HGERI has no confirmed clean support with growth")
        valid = [c for c in candidates if c.maturity is not GeriMaturity.INVALIDATED]
        # N3 recency decides between complete valid patterns. An incomplete
        # newer pivot never hides an existing valid N1/N2/N3 pattern.
        result = max(
            valid or candidates,
            key=lambda c: (
                len(c.levels) == 3,
                c.levels[-1].source_at,
                c.levels[0].source_at,
            ),
        )
        fib = self._fibonacci(context, bars, result)
        return result.model_copy(
            update={
                "metrics": (
                    *result.metrics,
                    NamedValue(
                        name="structural_selection_policy", value="LATEST_VALID_N3_THEN_LATEST_N1"
                    ),
                    NamedValue(name="structural_candidate_count", value=len(candidates)),
                    *fib,
                ),
            }
        )

    def _candidate_seed(
        self,
        context: Swing4HGeriContext,
        bars: tuple[MarketBar, ...],
        index: int,
    ) -> GeriAssessment:
        source = bars[index]
        confirmed = bars[index + self._pivot_radius]
        peak = max(bars[index : index + self._pivot_radius + 1], key=lambda b: b.high)
        return GeriAssessment(
            symbol=context.symbol,
            occurred_at=confirmed.timestamp,
            engine_version=self.engine_version,
            structure_policy="clean_support_swing",
            maturity=GeriMaturity.BUILDING,
            current_price=confirmed.close,
            levels=(
                GeriStructuralLevel(
                    sequence=1,
                    kind=GeriLevelKind.SUPPORT,
                    price=source.low,
                    source_at=source.timestamp,
                    confirmed_at=confirmed.timestamp,
                ),
            ),
            active_level_sequence=1,
            active_level_kind=GeriLevelKind.SUPPORT,
            active_level_price=source.low,
            atr14=self._atr(bars[: index + self._pivot_radius + 1]),
            breakout_buffer=Decimal(0),
            standalone_swing=True,
            trade_side=TradeSide.LONG,
            reasons=("clean_n1_candidate",),
            context_hash=f"sha256:{'0' * 64}",
            metrics=(
                NamedValue(name="clean_support_peak_price", value=peak.high),
                NamedValue(name="clean_support_peak_at", value=peak.timestamp.isoformat()),
            ),
        )

    def _fibonacci(
        self,
        context: Swing4HGeriContext,
        bars: tuple[MarketBar, ...],
        result: GeriAssessment,
    ) -> tuple[NamedValue, ...]:
        prefix = "structural_fibonacci_"
        prior = context.active_structure
        # Freeze the contextual anchors once the same N3 exists, even when
        # older candles fall outside the rolling input window.
        if (
            prior is not None
            and prior.engine_version == self.engine_version
            and len(prior.levels) == len(result.levels) == 3
            and prior.levels[0].source_at == result.levels[0].source_at
            and prior.levels[-1].source_at == result.levels[-1].source_at
        ):
            saved = tuple(m for m in prior.metrics if m.name.startswith(prefix))
            if saved:
                return saved
        metrics: dict[str, Decimal | str | bool] = {
            "policy": "DOMINANT_PRE_N3_IMPULSE_38_2_TO_61_8",
            "confluence": False,
            "status": "UNAVAILABLE",
            "required": False,
        }
        if len(result.levels) != 3:
            return tuple(NamedValue(name=prefix + k, value=v) for k, v in metrics.items())
        n3 = result.levels[-1]
        evidence = tuple(b for b in bars if b.timestamp < n3.source_at)
        if len(evidence) < 2:
            return tuple(NamedValue(name=prefix + k, value=v) for k, v in metrics.items())
        # Context is selected independently of GERI and of the price's
        # proximity to Fibonacci: highest high, then preceding lowest low.
        high_index = max(range(len(evidence)), key=lambda i: evidence[i].high)
        if high_index == 0:
            return tuple(NamedValue(name=prefix + k, value=v) for k, v in metrics.items())
        high = evidence[high_index]
        low = min(evidence[:high_index], key=lambda b: b.low)
        span = high.high - low.low
        if span <= 0:
            return tuple(NamedValue(name=prefix + k, value=v) for k, v in metrics.items())
        zone_low = high.high - Decimal("0.618") * span
        zone_high = high.high - Decimal("0.382") * span
        metrics.update(
            status="AVAILABLE",
            low=low.low,
            high=high.high,
            low_at=low.timestamp.isoformat(),
            high_at=high.timestamp.isoformat(),
            zone_low=zone_low,
            zone_high=zone_high,
            retracement=(high.high - n3.price) / span,
            confluence=zone_low <= n3.price <= zone_high,
        )
        return tuple(NamedValue(name=prefix + k, value=v) for k, v in metrics.items())
