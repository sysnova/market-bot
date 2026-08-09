"""Operational report for open Entry Watcher paper trades and audited outcomes."""

from __future__ import annotations

from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.common.settings import AppSettings, Environment
from app.contracts import (
    AnalysisHorizon,
    EntryCheckpointStatus,
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
    }


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
                "status": leg.status.value,
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


def _average_rate(numerator: int, denominator: int) -> str | None:
    if denominator == 0:
        return None
    return str(
        (Decimal(numerator) / Decimal(denominator) * Decimal("100")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    )
