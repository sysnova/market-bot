# pyright: reportPrivateUsage=false
"""Countertrend observation stays separate from paper entries, which start at CT2."""

from decimal import Decimal

from app.contracts import (
    EntryOpportunity,
    EntryOpportunityEvent,
    EntrySignal,
    EntrySignalFamily,
    GeriCountertrendMaturity,
)

from .engine import (
    EntryOpportunityEngineV11,
    _any_countertrend_paper_open,
    _countertrend_reference,
    _ct_progress,
    _replace_signal_reference,
    _signal_reference_for_setup,
)

_WATCH = {GeriCountertrendMaturity.CT0, GeriCountertrendMaturity.CT1}
_ENTRY = {GeriCountertrendMaturity.CT2, GeriCountertrendMaturity.CT3, GeriCountertrendMaturity.CT4}


class EntryOpportunityEngineV12(EntryOpportunityEngineV11):
    engine_version = "12.0.0"

    async def ingest_signal(self, signal: EntrySignal) -> tuple[EntryOpportunityEvent, ...]:
        if signal.family is EntrySignalFamily.GERI_COUNTERTREND and (
            signal.countertrend_maturity in _ENTRY
        ):
            stop, target = signal.invalidation, min(signal.targets, default=None)
            valid = stop is not None and target is not None and stop < signal.entry_price < target
            if not valid or (
                stop is not None
                and target is not None
                and (target - signal.entry_price) / (signal.entry_price - stop) <= Decimal("1.5")
            ):
                signal = signal.model_copy(
                    update={
                        "countertrend_maturity": None,
                        "reasons": (
                            *signal.reasons,
                            "countertrend_ineligible",
                            "insufficient_reward_risk",
                        ),
                    }
                )
        return await super().ingest_signal(signal)

    def _new_countertrend_opportunity(self, signal: EntrySignal) -> EntryOpportunity:
        if signal.countertrend_maturity not in _WATCH:
            return super()._new_countertrend_opportunity(signal)
        stage = signal.countertrend_maturity
        assert stage is not None
        watching = super()._new_countertrend_opportunity(
            signal.model_copy(
                update={
                    "countertrend_maturity": GeriCountertrendMaturity.CT0,
                }
            )
        )
        return watching.model_copy(
            update={
                "checkpoints": (),
                "progress_percent": _ct_progress(stage),
                "signal_references": (_countertrend_reference(signal, None),),
            }
        )

    def _apply_countertrend(
        self,
        active: EntryOpportunity,
        signal: EntrySignal,
    ) -> tuple[EntryOpportunity | None, str]:
        if signal.countertrend_maturity not in _WATCH:
            return super()._apply_countertrend(active, signal)
        assert signal.countertrend_maturity is not None
        previous = _signal_reference_for_setup(active, signal)
        if previous is not None and signal.created_at <= previous.created_at:
            return None, "stale_recovery_observation"
        if previous is None and _any_countertrend_paper_open(active):
            return None, "new_countertrend_setup_ignored_while_paper_open"
        updates: dict[str, object] = {
            "signal_references": _replace_signal_reference(
                active.signal_references, _countertrend_reference(signal, previous)
            ),
            "current_price": signal.entry_price,
            "updated_at": signal.created_at,
            "revision": active.revision + 1,
        }
        if active.primary_signal_family is EntrySignalFamily.GERI_COUNTERTREND:
            updates["progress_percent"] = _ct_progress(signal.countertrend_maturity)
        return active.model_copy(update=updates), "countertrend_observation_only"
