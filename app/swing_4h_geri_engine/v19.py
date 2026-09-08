# pyright: reportPrivateUsage=false
"""Separate LONG recovery and SHORT breakdown, with independent evidence and maturity."""

import hashlib
from dataclasses import replace
from decimal import Decimal

from app.contracts import GeriAssessment, GeriMaturity, NamedValue

from .engine import Swing4HGeriEngineV12, _validate_temporal_context
from .models import Swing4HGeriContext
from .recovery import RecoveryRules, recovery_metrics
from .short_structure import ShortRules, short_metrics
from .tactical_levels import validate_context
from .v15 import _support_contribution
from .v18 import Swing4HGeriEngineV18


class Swing4HGeriEngineV19(Swing4HGeriEngineV18):
    engine_version = "1.9.0"

    def __init__(
        self,
        *,
        structural_rebase_atr: Decimal = Decimal("3.00"),
        recovery_stop_atr: Decimal = Decimal("0.10"),
        recovery_maximum_risk_percent: Decimal = Decimal("4"),
        recovery_acceptance_bars: int = 1,
        short_minimum_rvol: Decimal = Decimal("1.5"),
        short_rvol_sessions: int = 5,
        **kwargs: object,
    ) -> None:
        super().__init__(structural_rebase_atr=structural_rebase_atr, **kwargs)
        if (
            min(recovery_stop_atr, short_minimum_rvol) <= 0
            or not (0 < recovery_maximum_risk_percent < 100)
            or min(recovery_acceptance_bars, short_rvol_sessions) < 1
        ):
            raise ValueError("recovery and SHORT rules must be positive and risk below 100%")
        self._recovery = RecoveryRules(
            minimum_rr=self._countertrend_minimum_rr,
            ttl_sessions=self._countertrend_ttl_sessions,
            breakout_atr=self._breakout_atr,
            stop_atr=recovery_stop_atr,
            zone_atr=self._zone_atr,
            maximum_extension_atr=self._maximum_extension_atr,
            maximum_risk_percent=recovery_maximum_risk_percent,
            acceptance_bars=recovery_acceptance_bars,
        )
        self._short = ShortRules(
            short_minimum_rvol, self._countertrend_minimum_rr, short_rvol_sessions
        )

    def analyze(self, context: Swing4HGeriContext) -> GeriAssessment:
        _validate_temporal_context(context)
        validate_context(context)
        # Deliberately bypass V13's inverse-side tactical path.
        structural = Swing4HGeriEngineV12.analyze(self, context)
        if structural.maturity in {GeriMaturity.BUILDING, GeriMaturity.EXTENDED} and (
            abs(structural.active_level_price - context.current_price) / structural.atr14
            > self._structural_rebase_atr
        ):
            recent: list[GeriAssessment] = []
            for start in range(1, len(context.bars) - self._minimum_bars + 1):
                try:
                    candidate = Swing4HGeriEngineV12.analyze(
                        self,
                        replace(context, bars=context.bars[start:], active_structure=None),
                    )
                except ValueError:
                    continue
                if candidate.levels[-1].confirmed_at > structural.levels[-1].confirmed_at and (
                    abs(candidate.active_level_price - context.current_price) / candidate.atr14
                    <= self._structural_rebase_atr
                ):
                    recent.append(candidate)
            if recent:
                updated = max(recent, key=lambda a: a.levels[-1].confirmed_at)
                structural = updated.model_copy(
                    update={
                        "reasons": (*updated.reasons, "structural_chain_rebased"),
                        "metrics": (
                            *updated.metrics,
                            NamedValue(
                                name="rebase_previous_active_level",
                                value=structural.active_level_price,
                            ),
                        ),
                    }
                )
        result = structural.model_copy(
            update={
                "metrics": (
                    *structural.metrics,
                    *self._recovery_metrics(context),
                    *short_metrics(context, self._short),
                    NamedValue(name="atr_duration_normalized", value=True),
                )
            }
        )
        contribution = _support_contribution(
            context,
            result,
            freshness_days=self._support_freshness_days,
            classifier=self._classify_support,
        )
        if contribution is not None:
            support, strength, zone = contribution
            result = result.model_copy(
                update={
                    "metrics": (
                        *result.metrics,
                        *self._support_metrics(support, strength, zone),
                    )
                }
            )
        payload = "|".join(m.model_dump_json() for m in result.metrics)
        digest = hashlib.sha256(f"{result.context_hash}|{payload}".encode()).hexdigest()
        return result.model_copy(update={"context_hash": f"sha256:{digest}"})

    def _recovery_metrics(self, context: Swing4HGeriContext) -> tuple[NamedValue, ...]:
        return recovery_metrics(context, self._recovery)
