# pyright: reportPrivateUsage=false
"""An unfilled recovery remains observable while the next entry is being confirmed."""

from app.contracts import EntryOpportunity, EntryOpportunitySignalReference, EntrySignal

from .engine import _countertrend_reference, _replace_signal_reference
from .v12 import EntryOpportunityEngineV12


class EntryOpportunityEngineV13(EntryOpportunityEngineV12):
    engine_version = "13.0.0"

    def _apply_countertrend_loss(
        self,
        active: EntryOpportunity,
        signal: EntrySignal,
        previous: EntryOpportunitySignalReference | None,
        paper_open: bool,
    ) -> tuple[EntryOpportunity | None, str]:
        if (
            signal.policy_version != "1.10.0"
            or previous is None
            or any(
                r in signal.reasons for r in ("countertrend_invalidated", "countertrend_expired")
            )
        ):
            return super()._apply_countertrend_loss(active, signal, previous, paper_open)
        if signal.created_at <= previous.created_at:
            return None, "stale_recovery_observation"
        reference = _countertrend_reference(signal, previous)
        return active.model_copy(
            update={
                "signal_references": _replace_signal_reference(active.signal_references, reference),
                "current_price": signal.entry_price,
                "updated_at": max(active.updated_at, signal.created_at),
                "revision": active.revision + 1,
            }
        ), "recovery_entry_pending_structure_tracked"
