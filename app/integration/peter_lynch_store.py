"""PostgreSQL watchlist persistence for the manual Peter Lynch screen."""

from __future__ import annotations

import json
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.peter_lynch_engine import PeterLynchEvaluation


class PeterLynchStoreError(RuntimeError):
    """Local watchlist reads or updates failed."""


@dataclass(frozen=True, slots=True)
class PeterLynchWorklist:
    """Active symbols remaining after current-analysis filtering."""

    symbols: tuple[str, ...]
    total: int
    skipped_current: int


class PostgresPeterLynchStore:
    def __init__(
        self,
        engine: AsyncEngine,
        *,
        customer_slug: str = "stock-analyzer",
        watchlist_code: str = "default",
    ) -> None:
        self._engine = engine
        self._customer_slug = customer_slug
        self._watchlist_code = watchlist_code

    async def load_symbols(self) -> tuple[str, ...]:
        """Compatibility view returning every active symbol without freshness filtering."""

        return (
            await self.load_worklist(
                as_of=date.max,
                ttl_days=0,
                engine_version="",
                policy_version="",
            )
        ).symbols

    async def load_worklist(
        self,
        *,
        as_of: date,
        ttl_days: int,
        engine_version: str,
        policy_version: str,
    ) -> PeterLynchWorklist:
        try:
            async with self._engine.connect() as connection:
                rows = (
                    await connection.execute(
                        text("""
                            select ws.symbol, ws.metadata_json
                            from stock.watchlist w
                            join stock.customer c on c.id = w.customer_id
                            join stock.watchlist_symbol ws on ws.watchlist_id = w.id
                            where c.slug = :customer_slug
                              and c.status = 'active'
                              and w.code = :watchlist_code
                              and w.status = 'active'
                              and ws.status = 'active'
                            order by ws.sort_order asc, ws.symbol asc
                        """),
                        {
                            "customer_slug": self._customer_slug,
                            "watchlist_code": self._watchlist_code,
                        },
                    )
                ).all()
        except SQLAlchemyError as error:
            raise PeterLynchStoreError("Local PostgreSQL watchlist query failed") from error
        symbols: list[str] = []
        skipped = 0
        seen: set[str] = set()
        for row in rows:
            symbol = str(row.symbol).strip().upper()
            if symbol in seen:
                continue
            seen.add(symbol)
            if ttl_days < 1:
                symbols.append(symbol)
                continue
            raw_metadata: object = row.metadata_json
            metadata: Mapping[str, object] = (
                cast("Mapping[str, object]", raw_metadata)
                if isinstance(raw_metadata, Mapping)
                else cast("dict[str, object]", {})
            )
            if lynch_analysis_is_current(
                metadata,
                as_of=as_of,
                ttl_days=ttl_days,
                engine_version=engine_version,
                policy_version=policy_version,
            ):
                skipped += 1
            else:
                symbols.append(symbol)
        return PeterLynchWorklist(
            symbols=tuple(symbols),
            total=len(seen),
            skipped_current=skipped,
        )

    async def save(self, evaluations: tuple[PeterLynchEvaluation, ...]) -> int:
        if not evaluations:
            return 0
        by_symbol = {item.symbol: item for item in evaluations}
        try:
            async with self._engine.begin() as connection:
                rows = (
                    await connection.execute(
                        text("""
                            select ws.id, ws.symbol, ws.metadata_json
                            from stock.watchlist w
                            join stock.customer c on c.id = w.customer_id
                            join stock.watchlist_symbol ws on ws.watchlist_id = w.id
                            where c.slug = :customer_slug
                              and c.status = 'active'
                              and w.code = :watchlist_code
                              and w.status = 'active'
                              and ws.status = 'active'
                              and ws.symbol = any(:symbols)
                            for update of ws
                        """),
                        {
                            "customer_slug": self._customer_slug,
                            "watchlist_code": self._watchlist_code,
                            "symbols": list(by_symbol),
                        },
                    )
                ).all()
                saved = 0
                for row in rows:
                    symbol = str(row.symbol).strip().upper()
                    evaluation = by_symbol.get(symbol)
                    if evaluation is None:
                        continue
                    raw_metadata = row.metadata_json
                    metadata = (
                        cast("dict[str, Any]", raw_metadata)
                        if isinstance(raw_metadata, dict)
                        else {}
                    )
                    await connection.execute(
                        text("""
                            update stock.watchlist_symbol
                            set metadata_json = cast(:metadata as jsonb), updated_at = now()
                            where id = :row_id
                        """),
                        {
                            "row_id": row.id,
                            "metadata": json.dumps(updated_metadata(metadata, evaluation)),
                        },
                    )
                    saved += 1
                return saved
        except SQLAlchemyError as error:
            raise PeterLynchStoreError("Local PostgreSQL watchlist update failed") from error


def updated_metadata(
    original: dict[str, Any], evaluation: PeterLynchEvaluation
) -> dict[str, Any]:
    """Return a current-only LYNCH merge without mutating caller-owned data."""

    metadata = deepcopy(original)
    raw_indicators: object = metadata.get("indicators", [])
    indicators: list[Any] = (
        list(cast("list[Any]", raw_indicators)) if isinstance(raw_indicators, list) else []
    )
    indicators = [item for item in indicators if item != "LYNCH"]
    if evaluation.eligible:
        indicators.append("LYNCH")
    metadata["indicators"] = indicators
    raw_details: object = metadata.get("indicatorDetails", {})
    details: dict[str, Any] = (
        dict(cast("Mapping[str, Any]", raw_details)) if isinstance(raw_details, dict) else {}
    )
    details["LYNCH"] = _evaluation_detail(evaluation)
    metadata["indicatorDetails"] = details
    return metadata


def lynch_analysis_is_current(
    metadata: Mapping[str, object],
    *,
    as_of: date,
    ttl_days: int,
    engine_version: str,
    policy_version: str,
) -> bool:
    """Return whether persisted LYNCH evidence is reusable for this exact policy."""

    if ttl_days < 1:
        return False
    raw_details = metadata.get("indicatorDetails")
    if not isinstance(raw_details, Mapping):
        return False
    details = cast("Mapping[object, object]", raw_details).get("LYNCH")
    if not isinstance(details, Mapping):
        return False
    values = cast("Mapping[object, object]", details)
    if values.get("engineVersion") != engine_version:
        return False
    if values.get("policyVersion") != policy_version:
        return False
    evaluated_at = values.get("evaluatedAt")
    if not isinstance(evaluated_at, str):
        return False
    try:
        evaluated_on = date.fromisoformat(evaluated_at)
    except ValueError:
        return False
    age_days = (as_of - evaluated_on).days
    return 0 <= age_days < ttl_days


def _evaluation_detail(evaluation: PeterLynchEvaluation) -> dict[str, Any]:
    metrics = {
        name: _json_decimal(value)
        for name, value in {
            "trailingPe": evaluation.metrics.trailing_pe,
            "projectedForwardEps": evaluation.metrics.projected_forward_eps,
            "projectedForwardPe": evaluation.metrics.projected_forward_pe,
            "debtToEquityPercent": evaluation.metrics.debt_to_equity_percent,
            "epsCagr3yPercent": evaluation.metrics.eps_cagr_percent,
            "peg": evaluation.metrics.peg,
            "marketCapUsd": evaluation.metrics.market_cap,
            "tangibleBookValue": evaluation.metrics.tangible_book_value,
            "marketCapToTangibleBook": evaluation.metrics.market_cap_to_tangible_book,
        }.items()
    }
    return {
        "engineVersion": evaluation.engine_version,
        "policyVersion": evaluation.policy_version,
        "evaluatedAt": evaluation.as_of.isoformat(),
        "priceAsOf": _json_date(evaluation.price_as_of),
        "fundamentalsAsOf": _json_date(evaluation.fundamentals_as_of),
        "latestInsiderPurchaseAt": _json_date(evaluation.latest_insider_purchase_at),
        "eligible": evaluation.eligible,
        "category": evaluation.category.value,
        "passedCount": evaluation.passed_count,
        "requiredCount": evaluation.required_count,
        "metrics": metrics,
        "criteria": [
            {
                "name": item.name.value,
                "passed": item.passed,
                "value": _json_decimal(item.value),
                "threshold": item.threshold,
                "reason": item.reason,
                "required": item.required,
            }
            for item in evaluation.criteria
        ],
        "forwardPeMethod": "price / (ttm_eps * (1 + eps_cagr_3y))",
        "forwardPeIsAnalystConsensus": False,
    }


def _json_decimal(value: Decimal | int | None) -> str | int | None:
    return str(value) if isinstance(value, Decimal) else value


def _json_date(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None
