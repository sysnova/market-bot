"""Operational report for open Entry Watcher paper trades and audited outcomes."""

from __future__ import annotations

from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.common.settings import AppSettings, Environment
from app.contracts import (
    AnalysisHorizon,
    EntryCheckpointStatus,
    EntryMaturityCheckpoint,
    EntryMaturityLevel,
    EntryOpportunity,
    EntryOpportunityStatus,
    EntrySignalFamily,
)
from app.persistence import create_database_engine, create_session_factory

from .entry_opportunity_store import PostgresEntryOpportunityStore

_MATURE_LEVELS = (
    EntryMaturityLevel.L1,
    EntryMaturityLevel.L2,
    EntryMaturityLevel.L3,
    EntryMaturityLevel.L4,
)
_TRACKING_LEVELS = (
    EntryMaturityLevel.ARMED,
    EntryMaturityLevel.IN_ZONE,
)
_FIXED_HORIZONS = (
    ("15m", "return_15m"),
    ("30m", "return_30m"),
    ("60m", "return_60m"),
    ("close", "return_close"),
)


async def load_entry_opportunity_report(*, history: int = 5000) -> dict[str, Any]:
    settings = AppSettings()
    database = create_database_engine(
        settings.database_url.get_secret_value(),
        require_ssl=settings.environment is Environment.PRODUCTION,
    )
    try:
        store = PostgresEntryOpportunityStore(create_session_factory(database))
        if not await store.is_ready():
            raise RuntimeError(
                "entry opportunity schema is unavailable; apply "
                "20260807010000_entry_opportunity_lifecycle.sql"
            )
        opportunities = await store.list_recent(limit=history)
        return build_entry_opportunity_report(opportunities)
    finally:
        await database.dispose()


def build_entry_opportunity_report(
    opportunities: tuple[EntryOpportunity, ...],
) -> dict[str, Any]:
    active = tuple(
        item for item in opportunities if item.status is not EntryOpportunityStatus.CLOSED
    )
    closed = tuple(
        item for item in opportunities if item.status is EntryOpportunityStatus.CLOSED
    )
    return {
        "summary": {
            "opportunities": len(opportunities),
            "open": len(active),
            "closed": len(closed),
        },
        "open_trades": [_open_trade(item) for item in sorted(active, key=lambda row: row.symbol)],
        "maturity_outcomes": _maturity_outcomes(opportunities),
        "signal_family_outcomes": _signal_family_outcomes(opportunities),
        "horizon_outcomes": _horizon_outcomes(opportunities),
        "evidence_audit": _evidence_audit(opportunities),
    }


def render_entry_opportunity_evidence_audit(audit: dict[str, Any]) -> str:
    """Render the statistical audit without presenting references as trades."""

    sample = audit["sample"]
    tracking = audit["snapshot"]["tracking"]
    actionable = audit["snapshot"]["actionable"]
    lines = [
        "AUDITORIA DE EVIDENCIA DE ENTRY OPPORTUNITY",
        (
            f"Muestra: {sample['opportunities']} opportunities | "
            f"{sample['checkpoints']} checkpoints | "
            f"{sample['closed_checkpoints']} cerrados"
        ),
        "",
        "REFERENCIAS (NO SON COMPRAS)",
        _human_snapshot_line(tracking),
        "",
        "ENTRADAS ACCIONABLES L1-L4",
        _human_snapshot_line(actionable),
        "",
        "RETORNOS FIJOS OBSERVADOS",
    ]
    for role, label in (("tracking", "Referencias"), ("actionable", "L1-L4")):
        pieces: list[str] = []
        for horizon, _ in _FIXED_HORIZONS:
            stats = audit["fixed_horizons"][role][horizon]
            pieces.append(
                f"{horizon}: n={stats['observed']} +{stats['positive']} "
                f"-{stats['negative']} prom={stats['average_percent'] or '-'}%"
            )
        lines.append(f"{label}: " + " | ".join(pieces))

    lines.extend(("", "EVIDENCIA CON P/L NEGATIVO"))
    if not audit["negative_evidence"]:
        lines.append("Sin checkpoints negativos en la marca actual/final.")
    for item in audit["negative_evidence"]:
        classes = ", ".join(str(value) for value in item["classifications"])
        lines.append(
            f"{item['symbol']} {item['level']} [{item['role']}] "
            f"{item['snapshot_return_percent']}% | MFE {item['mfe_percent']}% "
            f"MAE {item['mae_percent']}% | {classes}"
        )

    if audit["pullback_entry_improvement"]:
        lines.extend(("", "MEJORA DE REFERENCIA ARMED -> IN_ZONE"))
        for item in audit["pullback_entry_improvement"]:
            advantage = item["snapshot_advantage_percent"]
            advantage_text = (
                f"{advantage} pp"
                if advantage is not None
                else "no comparable (marcas de distinto momento)"
            )
            lines.append(
                f"{item['symbol']}: precio {item['armed_reference_price']} -> "
                f"{item['in_zone_reference_price']} | mejora de entrada "
                f"{item['entry_price_improvement_percent']}% | ventaja de P/L "
                f"{advantage_text}"
            )

    lines.extend(("", "LIMITACIONES"))
    lines.extend(f"- {item}" for item in audit["limitations"])
    return "\n".join(lines)


def _evidence_audit(opportunities: tuple[EntryOpportunity, ...]) -> dict[str, Any]:
    checkpoints = tuple(
        (opportunity, checkpoint)
        for opportunity in opportunities
        for checkpoint in opportunity.checkpoints
    )
    tracking = tuple(
        pair for pair in checkpoints if pair[1].level in _TRACKING_LEVELS
    )
    actionable = tuple(
        pair for pair in checkpoints if pair[1].level in _MATURE_LEVELS
    )
    negative: list[dict[str, Any]] = []
    for opportunity, checkpoint in checkpoints:
        snapshot_return = _checkpoint_snapshot_return(checkpoint)
        if snapshot_return >= 0:
            continue
        observed = tuple(
            value
            for _, field in _FIXED_HORIZONS
            if (value := getattr(checkpoint, field)) is not None
        )
        classifications: list[str] = []
        if checkpoint.status is EntryCheckpointStatus.OPEN:
            classifications.append("OPEN_RIGHT_CENSORED")
        if checkpoint.mfe_percent > 0:
            classifications.append("GAVE_BACK_POSITIVE_EXCURSION")
        if observed and all(value < 0 for value in observed):
            classifications.append("NEGATIVE_AT_ALL_OBSERVED_HORIZONS")
        elif _recovered_after_negative(observed):
            classifications.append(
                "TEMPORARY_RECOVERY_GAVE_BACK"
                if observed[-1] < 0
                else "RECOVERED_AT_A_LATER_HORIZON"
            )
        negative.append(
            {
                "symbol": opportunity.symbol,
                "level": checkpoint.level.value,
                "role": _checkpoint_role(checkpoint),
                "status": checkpoint.status.value,
                "snapshot_return_percent": _decimal_text(snapshot_return),
                "mfe_percent": _decimal_text(checkpoint.mfe_percent),
                "mae_percent": _decimal_text(checkpoint.mae_percent),
                "observed_fixed_horizons": len(observed),
                "fixed_returns": {
                    label: (
                        _decimal_text(value)
                        if (value := getattr(checkpoint, field)) is not None
                        else None
                    )
                    for label, field in _FIXED_HORIZONS
                },
                "classifications": classifications,
            }
        )

    limitations = [
        "ARMED e IN_ZONE son referencias de maduración; no deben contarse como trades.",
        "Los checkpoints OPEN están censurados: su P/L puede cambiar y no es un resultado final.",
    ]
    if len(checkpoints) < 30:
        limitations.append(
            "La muestra tiene menos de 30 checkpoints; las tasas son exploratorias."
        )
    if not actionable:
        limitations.append(
            "Todavía no hay entradas L1-L4; no puede estimarse la tasa de acierto de compras."
        )
    if not any(
        checkpoint.status is EntryCheckpointStatus.CLOSED
        for _, checkpoint in checkpoints
    ):
        limitations.append(
            "No hay checkpoints cerrados; aún no existe una distribución de resultados finales."
        )

    return {
        "sample": {
            "opportunities": len(opportunities),
            "checkpoints": len(checkpoints),
            "tracking_references": len(tracking),
            "actionable_entries": len(actionable),
            "open_checkpoints": sum(
                checkpoint.status is EntryCheckpointStatus.OPEN
                for _, checkpoint in checkpoints
            ),
            "closed_checkpoints": sum(
                checkpoint.status is EntryCheckpointStatus.CLOSED
                for _, checkpoint in checkpoints
            ),
        },
        "snapshot": {
            "tracking": _snapshot_stats(tracking),
            "actionable": _snapshot_stats(actionable),
        },
        "fixed_horizons": {
            "tracking": _fixed_horizon_stats(tracking),
            "actionable": _fixed_horizon_stats(actionable),
        },
        "negative_evidence": sorted(
            negative,
            key=lambda item: Decimal(item["snapshot_return_percent"]),
        ),
        "pullback_entry_improvement": _pullback_entry_improvement(opportunities),
        "limitations": limitations,
    }


def _snapshot_stats(
    pairs: tuple[tuple[EntryOpportunity, EntryMaturityCheckpoint], ...],
) -> dict[str, Any]:
    values = tuple(_checkpoint_snapshot_return(checkpoint) for _, checkpoint in pairs)
    positive = sum(value > 0 for value in values)
    negative = sum(value < 0 for value in values)
    return {
        "observed": len(values),
        "positive": positive,
        "negative": negative,
        "breakeven": len(values) - positive - negative,
        "positive_rate_percent": _average_rate(positive, len(values)),
        "average_percent": _average(values),
        "median_percent": _median(values),
        "average_mfe_percent": _average(
            tuple(checkpoint.mfe_percent for _, checkpoint in pairs)
        ),
        "average_mae_percent": _average(
            tuple(checkpoint.mae_percent for _, checkpoint in pairs)
        ),
    }


def _fixed_horizon_stats(
    pairs: tuple[tuple[EntryOpportunity, EntryMaturityCheckpoint], ...],
) -> dict[str, Any]:
    result: dict[str, dict[str, Any]] = {}
    for label, field in _FIXED_HORIZONS:
        values = tuple(
            value
            for _, checkpoint in pairs
            if (value := getattr(checkpoint, field)) is not None
        )
        positive = sum(value > 0 for value in values)
        negative = sum(value < 0 for value in values)
        result[label] = {
            "observed": len(values),
            "positive": positive,
            "negative": negative,
            "breakeven": len(values) - positive - negative,
            "positive_rate_percent": _average_rate(positive, len(values)),
            "average_percent": _average(values),
            "median_percent": _median(values),
        }
    return result


def _pullback_entry_improvement(
    opportunities: tuple[EntryOpportunity, ...],
) -> list[dict[str, Any]]:
    comparisons: list[dict[str, Any]] = []
    for opportunity in opportunities:
        armed = next(
            (
                checkpoint
                for checkpoint in opportunity.checkpoints
                if checkpoint.level is EntryMaturityLevel.ARMED
                and checkpoint.signal_family is EntrySignalFamily.CORE_ENTRY
            ),
            None,
        )
        in_zone = next(
            (
                checkpoint
                for checkpoint in opportunity.checkpoints
                if checkpoint.level is EntryMaturityLevel.IN_ZONE
                and checkpoint.signal_family is EntrySignalFamily.CORE_ENTRY
            ),
            None,
        )
        if armed is None or in_zone is None:
            continue
        improvement = (armed.entry_price - in_zone.entry_price) / armed.entry_price * 100
        marks_comparable = armed.current_price == in_zone.current_price
        advantage = (
            _checkpoint_snapshot_return(in_zone) - _checkpoint_snapshot_return(armed)
            if marks_comparable
            else None
        )
        comparisons.append(
            {
                "symbol": opportunity.symbol,
                "armed_reference_price": str(armed.entry_price),
                "in_zone_reference_price": str(in_zone.entry_price),
                "entry_price_improvement_percent": _decimal_text(improvement),
                "snapshot_advantage_percent": (
                    _decimal_text(advantage) if advantage is not None else None
                ),
                "marks_comparable": marks_comparable,
            }
        )
    return sorted(comparisons, key=lambda item: item["symbol"])


def _checkpoint_snapshot_return(checkpoint: EntryMaturityCheckpoint) -> Decimal:
    if checkpoint.status is EntryCheckpointStatus.CLOSED:
        assert checkpoint.gain_loss_percent is not None
        return checkpoint.gain_loss_percent
    return (checkpoint.current_price / checkpoint.entry_price - Decimal("1")) * Decimal(
        "100"
    )


def _checkpoint_role(checkpoint: EntryMaturityCheckpoint) -> str:
    if checkpoint.level in _TRACKING_LEVELS:
        return "TRACKING_REFERENCE"
    return "ACTIONABLE_ENTRY"


def _recovered_after_negative(values: tuple[Decimal, ...]) -> bool:
    negative_seen = False
    for value in values:
        if value < 0:
            negative_seen = True
        elif value > 0 and negative_seen:
            return True
    return False


def _human_snapshot_line(stats: dict[str, Any]) -> str:
    return (
        f"n={stats['observed']} | positivas={stats['positive']} | "
        f"negativas={stats['negative']} | neutras={stats['breakeven']} | "
        f"promedio={stats['average_percent'] or '-'}% | "
        f"mediana={stats['median_percent'] or '-'}%"
    )


def _decimal_text(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def _open_trade(opportunity: EntryOpportunity) -> dict[str, Any]:
    filled = min(10, max(0, int(opportunity.progress_percent / Decimal("10"))))
    core_family = opportunity.primary_signal_family.value.startswith("CORE_")
    return {
        "symbol": opportunity.symbol,
        "status": opportunity.status.value,
        "signal_family": opportunity.primary_signal_family.value,
        "maturity": opportunity.current_maturity.value if core_family else None,
        "peak_maturity": opportunity.peak_maturity.value if core_family else None,
        "progress_percent": str(opportunity.progress_percent),
        "progress_bar": f"[{'#' * filled}{'-' * (10 - filled)}]",
        "current_price": str(opportunity.current_price),
        "original_price": str(opportunity.original_price),
        "zone": [str(opportunity.zone_low), str(opportunity.zone_high)],
        "invalidation": str(opportunity.invalidation),
        "expires_at": opportunity.expires_at.isoformat(),
        "legs": [
            {
                "horizon": leg.horizon.value,
                "setup_id": leg.setup_id,
                "status": leg.status.value,
                "expires_at": leg.expires_at.isoformat() if leg.expires_at is not None else None,
                "entry_price": str(leg.entry_price) if leg.entry_price is not None else None,
                "current_price": str(leg.current_price),
                "gain_loss_percent": (
                    str(leg.gain_loss_percent) if leg.gain_loss_percent is not None else None
                ),
                "mfe_percent": str(leg.mfe_percent),
                "mae_percent": str(leg.mae_percent),
            }
            for leg in opportunity.legs
        ],
    }


def _maturity_outcomes(opportunities: tuple[EntryOpportunity, ...]) -> dict[str, Any]:
    values: dict[EntryMaturityLevel, list[Any]] = defaultdict(list)
    for opportunity in opportunities:
        for checkpoint in opportunity.checkpoints:
            if checkpoint.level in _MATURE_LEVELS:
                values[checkpoint.level].append(checkpoint)
    return {
        level.value: _outcome_stats(
            tuple(
                item.gain_loss_percent
                for item in values[level]
                if item.gain_loss_percent is not None
            ),
            open_count=sum(
                item.status is EntryCheckpointStatus.OPEN for item in values[level]
            ),
            mfe=tuple(item.mfe_percent for item in values[level]),
            mae=tuple(item.mae_percent for item in values[level]),
        )
        for level in _MATURE_LEVELS
    }


def _horizon_outcomes(opportunities: tuple[EntryOpportunity, ...]) -> dict[str, Any]:
    return {
        horizon.value: _outcome_stats(
            tuple(
                leg.gain_loss_percent
                for opportunity in opportunities
                for leg in opportunity.legs
                if leg.horizon is horizon and leg.gain_loss_percent is not None
            ),
            open_count=sum(
                leg.status.value in {"WATCHING", "OPEN"}
                for opportunity in opportunities
                for leg in opportunity.legs
                if leg.horizon is horizon
            ),
            mfe=tuple(
                leg.mfe_percent
                for opportunity in opportunities
                for leg in opportunity.legs
                if leg.horizon is horizon
            ),
            mae=tuple(
                leg.mae_percent
                for opportunity in opportunities
                for leg in opportunity.legs
                if leg.horizon is horizon
            ),
        )
        for horizon in (
            AnalysisHorizon.INTRADAY,
            AnalysisHorizon.SWING,
            AnalysisHorizon.LONG_TERM,
        )
    }


def _signal_family_outcomes(
    opportunities: tuple[EntryOpportunity, ...],
) -> dict[str, Any]:
    checkpoints = tuple(
        checkpoint
        for opportunity in opportunities
        for checkpoint in opportunity.checkpoints
    )
    return {
        family.value: _outcome_stats(
            tuple(
                item.gain_loss_percent
                for item in checkpoints
                if item.signal_family is family and item.gain_loss_percent is not None
            ),
            open_count=sum(
                item.signal_family is family and item.status is EntryCheckpointStatus.OPEN
                for item in checkpoints
            ),
            mfe=tuple(
                item.mfe_percent for item in checkpoints if item.signal_family is family
            ),
            mae=tuple(
                item.mae_percent for item in checkpoints if item.signal_family is family
            ),
        )
        for family in EntrySignalFamily
    }


def _outcome_stats(
    gain_loss: tuple[Decimal, ...],
    *,
    open_count: int,
    mfe: tuple[Decimal, ...],
    mae: tuple[Decimal, ...],
) -> dict[str, Any]:
    wins = sum(value > 0 for value in gain_loss)
    losses = sum(value < 0 for value in gain_loss)
    breakeven = len(gain_loss) - wins - losses
    return {
        "open": open_count,
        "closed": len(gain_loss),
        "wins": wins,
        "losses": losses,
        "breakeven": breakeven,
        "success_rate_percent": _average_rate(wins, len(gain_loss)),
        "average_gain_loss_percent": _average(gain_loss),
        "average_mfe_percent": _average(mfe),
        "average_mae_percent": _average(mae),
    }


def _average(values: tuple[Decimal, ...]) -> str | None:
    if not values:
        return None
    return str(
        (sum(values, Decimal("0")) / Decimal(len(values))).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )
    )


def _median(values: tuple[Decimal, ...]) -> str | None:
    if not values:
        return None
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        value = ordered[midpoint]
    else:
        value = (ordered[midpoint - 1] + ordered[midpoint]) / Decimal("2")
    return _decimal_text(value)


def _average_rate(numerator: int, denominator: int) -> str | None:
    if denominator == 0:
        return None
    return str(
        (Decimal(numerator) / Decimal(denominator) * Decimal("100")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    )
