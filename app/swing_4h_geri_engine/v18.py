"""Structural-chain rebasing for standalone 4HGERI v1.8."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from decimal import Decimal

from app.contracts import GeriAssessment, GeriMaturity, NamedValue

from .models import Swing4HGeriContext
from .v16 import Swing4HGeriEngineV16

_DETACHED_MATURITIES = {
    GeriMaturity.EXTENDED,
    GeriMaturity.RECLAIM_REQUIRED,
    GeriMaturity.INVALIDATED,
}


class Swing4HGeriEngineV18(Swing4HGeriEngineV16):
    """Replace a severely detached pinned chain with the latest relevant N1/N2/N3."""

    engine_version = "1.8.0"

    def __init__(
        self,
        *,
        structural_rebase_atr: Decimal = Decimal("3.00"),
        **kwargs: object,
    ) -> None:
        if structural_rebase_atr <= 0:
            raise ValueError("structural rebase ATR must be positive")
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._structural_rebase_atr = structural_rebase_atr

    def analyze(self, context: Swing4HGeriContext) -> GeriAssessment:
        pinned = Swing4HGeriEngineV16.analyze(self, context)
        distance_atr = abs(pinned.active_level_price - pinned.current_price) / pinned.atr14
        if (
            pinned.maturity is not GeriMaturity.EXTENDED
            or distance_atr <= self._structural_rebase_atr
        ):
            return pinned.model_copy(update={"engine_version": self.engine_version})

        candidates: list[tuple[int, GeriAssessment]] = []
        final_bars = tuple(bar for bar in context.bars[-self._lookback :] if bar.is_final)
        for start in range(1, len(final_bars) - self._minimum_bars + 1):
            suffix = final_bars[start:]
            try:
                candidate = Swing4HGeriEngineV16.analyze(
                    self,
                    replace(context, bars=suffix, active_structure=None),
                )
            except ValueError:
                continue
            if candidate.maturity in _DETACHED_MATURITIES:
                continue
            candidates.append((start, candidate))

        if not candidates:
            return pinned.model_copy(update={"engine_version": self.engine_version})

        _, rebased = max(candidates, key=_rebase_candidate_priority)
        rebased_distance_atr = (
            abs(rebased.active_level_price - rebased.current_price) / rebased.atr14
        )
        return rebased.model_copy(
            update={
                "engine_version": self.engine_version,
                "reasons": tuple(dict.fromkeys(("structural_chain_rebased", *rebased.reasons))),
                "metrics": (
                    *rebased.metrics,
                    NamedValue(
                        name="rebase_previous_active_level",
                        value=pinned.active_level_price,
                    ),
                    NamedValue(
                        name="rebase_previous_distance_atr",
                        value=distance_atr.quantize(Decimal("0.0001")),
                    ),
                    NamedValue(
                        name="rebase_active_distance_atr",
                        value=rebased_distance_atr.quantize(Decimal("0.0001")),
                    ),
                ),
                "context_hash": _rebased_hash(
                    rebased.context_hash,
                    pinned.context_hash,
                ),
            }
        )


def _rebase_candidate_priority(
    item: tuple[int, GeriAssessment],
) -> tuple[bool, object, object, int]:
    start, candidate = item
    actionable = candidate.maturity is not GeriMaturity.BUILDING
    active = candidate.levels[-1]
    return actionable, active.confirmed_at, candidate.levels[0].confirmed_at, start


def _rebased_hash(rebased_hash: str, pinned_hash: str) -> str:
    payload = f"{rebased_hash}|rebase:{pinned_hash}".encode()
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"
