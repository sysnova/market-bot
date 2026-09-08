"""Pure read-model projection for the filterable opportunity dashboard."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.contracts import (
    EntryCheckpointStatus,
    EntryMaturityCheckpoint,
    EntryMaturityLevel,
    EntryOpportunity,
    EntryOpportunitySignalReference,
    EntrySignalFamily,
    GeriCountertrendMaturity,
)

_FOUR_PLACES = Decimal("0.0001")
_CORE_FAMILIES = {EntrySignalFamily.CORE_ENTRY, EntrySignalFamily.CORE_RECOVERY}
_CORE_BUYS = {
    EntryMaturityLevel.L1,
    EntryMaturityLevel.L2,
    EntryMaturityLevel.L3,
    EntryMaturityLevel.L4,
}

_THESIS_LABELS = {
    EntrySignalFamily.CORE_ENTRY: "Entrada Core",
    EntrySignalFamily.CORE_RECOVERY: "Recuperación Core",
    EntrySignalFamily.PATREON_CAPS: "Patreon Caps",
    EntrySignalFamily.LONG_PORTFOLIO: "Portafolio Long",
    EntrySignalFamily.SIGNAL_FUSION: "Fusión de señales",
    EntrySignalFamily.PORTFOLIO_FLOW: "Flujo de portafolio",
    EntrySignalFamily.LEVERAGED_THESIS: "Tesis apalancada",
    EntrySignalFamily.SWING_TRADE: "SwingTrade Fibonacci",
    EntrySignalFamily.GERI_COUNTERTREND: "GERI Countertrend",
}


def checkpoint_pnl_percent(checkpoint: EntryMaturityCheckpoint) -> Decimal:
    """Return audited P/L for a close or mark-to-market P/L for an open checkpoint."""

    if checkpoint.status is EntryCheckpointStatus.CLOSED:
        assert checkpoint.gain_loss_percent is not None
        return checkpoint.gain_loss_percent
    return (checkpoint.current_price / checkpoint.entry_price - Decimal("1")) * Decimal("100")


def build_dashboard_snapshot(
    opportunities: Iterable[EntryOpportunity],
    *,
    refreshed_at: datetime,
    reasons_by_id: dict[str, tuple[str, ...]] | None = None,
) -> dict[str, Any]:
    """Flatten opportunities into independently filterable paper-entry observations."""

    reasons = reasons_by_id or {}
    items = tuple(opportunities)
    rows = [
        _checkpoint_row(opportunity, checkpoint, reasons.get(str(opportunity.opportunity_id), ()))
        for opportunity in items
        for checkpoint in opportunity.checkpoints
    ]
    rows.extend(
        _recovery_reference_row(opportunity, reference)
        for opportunity in items
        for reference in opportunity.signal_references
        if reference.family is EntrySignalFamily.GERI_COUNTERTREND
        and reference.policy_version == "1.9.0"
        and reference.current_ct in {GeriCountertrendMaturity.CT0, GeriCountertrendMaturity.CT1}
        and not any(cp.setup_id == reference.setup_id for cp in opportunity.checkpoints)
    )
    rows.sort(key=lambda row: (row["updated_at"], row["symbol"]), reverse=True)
    return {
        "type": "snapshot",
        "refreshed_at": refreshed_at.isoformat(),
        "opportunity_count": len(items),
        "rows": rows,
        "filters": {
            "symbols": sorted({row["symbol"] for row in rows}),
            "theses": sorted(
                ({"value": row["thesis"], "label": row["thesis_label"]} for row in rows),
                key=lambda item: item["label"],
            ),
            "states": sorted({row["state"] for row in rows}),
            "statuses": sorted({row["lifecycle_status"] for row in rows}),
        },
        "definitions": {
            "pnl": (
                "Cierres usan P/L auditado; abiertos usan mark-to-market desde el precio "
                "congelado al emitirse cada checkpoint. ARMED/IN_ZONE miden movimiento desde "
                "su propia referencia; cada compra y leg mide P/L desde su propia entrada. "
                "Los promedios son simples, sin position sizing."
            ),
            "reference": (
                "ARMED, IN_ZONE y CT0 son referencias; recuperación 1.9 también mantiene CT1 "
                "como observación sin entrada ni P/L."
            ),
            "buy": (
                "Core L1-L4, SwingTrade ST1-ST4, GERI histórico CT1-CT4; "
                "recuperación 1.9 entra desde CT2 con riesgo válido."
            ),
        },
    }


def _recovery_reference_row(
    opportunity: EntryOpportunity,
    reference: EntryOpportunitySignalReference,
) -> dict[str, Any]:
    """An observation has no entry or P/L; no synthetic financial checkpoint is created."""
    return {
        "row_id": f"{opportunity.opportunity_id}:reference:{reference.setup_id}",
        "opportunity_id": str(opportunity.opportunity_id),
        "symbol": opportunity.symbol,
        "thesis": reference.family.value,
        "thesis_label": "Countertrend recuperación LONG",
        "setup_id": reference.setup_id,
        "entry_kind": "REFERENCE",
        "state": reference.current_ct.value if reference.current_ct is not None else "WATCH",
        "lifecycle_status": opportunity.status.value,
        "checkpoint_status": "WATCHING",
        "outcome": None,
        "close_reason": None,
        "entry_price": None,
        "current_price": _number(opportunity.current_price),
        "exit_price": None,
        "pnl_percent": None,
        "pnl_basis": "NO_ENTRY",
        "mfe_percent": None,
        "mae_percent": None,
        "invalidation": None,
        "risk_to_invalidation_percent": None,
        "target": None,
        "target_distance_percent": None,
        "zone_low": None,
        "zone_high": None,
        "reached_at": reference.created_at.isoformat(),
        "updated_at": opportunity.updated_at.isoformat(),
        "closed_at": None,
        "is_losing": False,
        "latest_reasons": ["countertrend_observation_only"],
        "analysis_summary": [],
    }


def _checkpoint_row(
    opportunity: EntryOpportunity,
    checkpoint: EntryMaturityCheckpoint,
    latest_reasons: tuple[str, ...],
) -> dict[str, Any]:
    pnl = checkpoint_pnl_percent(checkpoint)
    family = checkpoint.signal_family
    state = _checkpoint_state(checkpoint)
    entry_kind = "BUY" if _is_buy(checkpoint) else "REFERENCE"
    target_distance = (
        (checkpoint.target / checkpoint.current_price - Decimal("1")) * Decimal("100")
        if checkpoint.target is not None
        else None
    )
    risk = (checkpoint.current_price / checkpoint.invalidation - Decimal("1")) * Decimal("100")
    analyses = sorted(opportunity.latest_analyses, key=lambda item: item.as_of, reverse=True)
    return {
        "row_id": str(checkpoint.checkpoint_id),
        "opportunity_id": str(opportunity.opportunity_id),
        "symbol": opportunity.symbol,
        "thesis": family.value,
        "thesis_label": _THESIS_LABELS[family],
        "setup_id": checkpoint.setup_id,
        "entry_kind": entry_kind,
        "state": state,
        "lifecycle_status": opportunity.status.value,
        "checkpoint_status": checkpoint.status.value,
        "outcome": checkpoint.outcome.value if checkpoint.outcome is not None else None,
        "close_reason": (
            opportunity.close_reason.value if opportunity.close_reason is not None else None
        ),
        "entry_price": _number(checkpoint.entry_price),
        "current_price": _number(checkpoint.current_price),
        "exit_price": _number(checkpoint.exit_price),
        "pnl_percent": _number(pnl),
        "pnl_basis": (
            "AUDITED_CLOSE" if checkpoint.status is EntryCheckpointStatus.CLOSED else "LIVE_MARK"
        ),
        "mfe_percent": _number(checkpoint.mfe_percent),
        "mae_percent": _number(checkpoint.mae_percent),
        "invalidation": _number(checkpoint.invalidation),
        "risk_to_invalidation_percent": _number(risk),
        "target": _number(checkpoint.target),
        "target_distance_percent": _number(target_distance),
        "zone_low": _number(checkpoint.zone_low),
        "zone_high": _number(checkpoint.zone_high),
        "reached_at": checkpoint.reached_at.isoformat(),
        "updated_at": opportunity.updated_at.isoformat(),
        "closed_at": checkpoint.closed_at.isoformat() if checkpoint.closed_at else None,
        "is_losing": pnl < 0,
        "latest_reasons": list(latest_reasons),
        "analysis_summary": [
            {
                "engine": item.engine_id,
                "horizon": item.horizon.value,
                "verdict": item.verdict.value,
                "direction": item.direction.value,
                "score": _number(item.score),
                "confidence": _number(item.confidence),
                "as_of": item.as_of.isoformat(),
            }
            for item in analyses[:8]
        ],
    }


def _checkpoint_state(checkpoint: EntryMaturityCheckpoint) -> str:
    if checkpoint.countertrend_maturity is not None:
        return checkpoint.countertrend_maturity.value
    if checkpoint.swing_trade_maturity is not None:
        return checkpoint.swing_trade_maturity.value
    return checkpoint.level.value


def _is_buy(checkpoint: EntryMaturityCheckpoint) -> bool:
    if checkpoint.signal_family in _CORE_FAMILIES:
        return checkpoint.level in _CORE_BUYS
    if checkpoint.signal_family is EntrySignalFamily.GERI_COUNTERTREND:
        return checkpoint.countertrend_maturity is not GeriCountertrendMaturity.CT0
    return True


def _number(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return str(value.quantize(_FOUR_PLACES, rounding=ROUND_HALF_UP))
