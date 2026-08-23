"""Paper-only lifecycle for independent intraday round trips."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from app.contracts import new_uuid7
from app.contracts.intraday_opportunity import (
    IntradayCloseReason,
    IntradayFill,
    IntradayFillRole,
    IntradayOpportunity,
    IntradayOpportunityEvent,
    IntradayOpportunityEventKind,
    IntradayOpportunityStatus,
    IntradaySide,
    IntradayTradeAction,
)

from .ports import IntradayOpportunityStore

_NEW_YORK = ZoneInfo("America/New_York")
_FOUR_PLACES = Decimal("0.0001")


class ActiveIntradayOpportunityError(RuntimeError):
    """Raised when a strategy tries to overlap round trips on the same symbol."""


class IntradayOpportunityEngine:
    """Track paper fills and P/L without broker or order-system dependencies."""

    engine_id = "intraday-opportunity"
    engine_version = "1.0.0"

    def __init__(
        self,
        *,
        store: IntradayOpportunityStore,
        id_factory: Callable[[], UUID] = new_uuid7,
        end_of_day_cutoff: time = time(15, 55),
    ) -> None:
        self._store = store
        self._id_factory = id_factory
        self._end_of_day_cutoff = end_of_day_cutoff

    async def open_position(
        self,
        *,
        source_event_id: UUID,
        symbol: str,
        strategy_id: str,
        side: IntradaySide,
        quantity: Decimal,
        bid: Decimal,
        ask: Decimal,
        stop_price: Decimal,
        target_price: Decimal,
        occurred_at: datetime,
        max_holding: timedelta,
        fee: Decimal = Decimal("0"),
    ) -> IntradayOpportunityEvent | None:
        """Open one round trip at the conservative executable quote side."""

        if await self._store.source_event_seen(source_event_id):
            return None
        _validate_quote_and_time(bid=bid, ask=ask, occurred_at=occurred_at)
        if quantity <= 0 or fee < 0 or max_holding <= timedelta(0):
            raise ValueError("quantity/max_holding must be positive and fee non-negative")
        if occurred_at.astimezone(_NEW_YORK).time() >= self._end_of_day_cutoff:
            raise ValueError("cannot open an intraday opportunity after the EOD cutoff")
        normalized_symbol = symbol.strip().upper()
        normalized_strategy = strategy_id.strip()
        active = await self._store.load_active(normalized_symbol, normalized_strategy)
        if active is not None:
            raise ActiveIntradayOpportunityError(
                f"active intraday opportunity exists for {normalized_symbol}/{normalized_strategy}"
            )
        opportunity_id = self._id_factory()
        entry_price = ask if side is IntradaySide.LONG else bid
        current_price = bid if side is IntradaySide.LONG else ask
        if (side is IntradaySide.LONG and current_price <= stop_price) or (
            side is IntradaySide.SHORT and current_price >= stop_price
        ):
            raise ValueError("conservative entry mark already breaches the stop")
        entry_action = (
            IntradayTradeAction.BUY
            if side is IntradaySide.LONG
            else IntradayTradeAction.SELL
        )
        fill = IntradayFill(
            fill_id=self._id_factory(),
            opportunity_id=opportunity_id,
            source_event_id=source_event_id,
            occurred_at=occurred_at,
            role=IntradayFillRole.ENTRY,
            action=entry_action,
            quantity=quantity,
            price=entry_price,
            fee=fee,
        )
        gross_pnl, gross_percent = _returns(
            side=side,
            entry_price=entry_price,
            mark_price=current_price,
            quantity=quantity,
        )
        net_pnl = gross_pnl - fee
        net_percent = _notional_percent(net_pnl, entry_price * quantity)
        opportunity = IntradayOpportunity(
            opportunity_id=opportunity_id,
            symbol=normalized_symbol,
            strategy_id=normalized_strategy,
            session_date=occurred_at.astimezone(_NEW_YORK).date(),
            side=side,
            status=IntradayOpportunityStatus.OPEN,
            opened_at=occurred_at,
            updated_at=occurred_at,
            expires_at=occurred_at + max_holding,
            quantity=quantity,
            entry_price=entry_price,
            current_price=current_price,
            stop_price=stop_price,
            target_price=target_price,
            highest_mark=current_price,
            lowest_mark=current_price,
            gross_pnl=gross_pnl,
            net_pnl=net_pnl,
            gross_pnl_percent=gross_percent,
            net_pnl_percent=net_percent,
            mfe_percent=max(Decimal("0"), gross_percent),
            mae_percent=min(Decimal("0"), gross_percent),
            fees_total=fee,
            source_signal_id=source_event_id,
            entry_fill=fill,
        )
        event = IntradayOpportunityEvent(
            event_id=self._id_factory(),
            source_event_id=source_event_id,
            kind=IntradayOpportunityEventKind.OPENED,
            occurred_at=occurred_at,
            opportunity=opportunity,
            reasons=("paper_position_opened", "conservative_quote_fill"),
            fill=fill,
        )
        await self._store.save(opportunity, event)
        return event

    async def mark_quote(
        self,
        *,
        source_event_id: UUID,
        symbol: str,
        strategy_id: str,
        bid: Decimal,
        ask: Decimal,
        occurred_at: datetime,
        exit_fee: Decimal = Decimal("0"),
    ) -> IntradayOpportunityEvent | None:
        """Mark at the liquidating quote and apply stop, target, time and EOD controls."""

        if await self._store.source_event_seen(source_event_id):
            return None
        _validate_quote_and_time(bid=bid, ask=ask, occurred_at=occurred_at)
        if exit_fee < 0:
            raise ValueError("exit fee cannot be negative")
        active = await self._store.load_active(symbol, strategy_id)
        if active is None:
            return None
        if occurred_at < active.updated_at:
            return None
        mark_price = bid if active.side is IntradaySide.LONG else ask
        reason = _automatic_close_reason(
            active,
            mark_price=mark_price,
            occurred_at=occurred_at,
            end_of_day_cutoff=self._end_of_day_cutoff,
        )
        if reason is not None:
            return await self._close(
                active,
                source_event_id=source_event_id,
                mark_price=mark_price,
                occurred_at=occurred_at,
                reason=reason,
                exit_fee=exit_fee,
            )
        opportunity = _mark(active, mark_price=mark_price, occurred_at=occurred_at)
        event = IntradayOpportunityEvent(
            event_id=self._id_factory(),
            source_event_id=source_event_id,
            kind=IntradayOpportunityEventKind.MARKED,
            occurred_at=occurred_at,
            opportunity=opportunity,
            reasons=("paper_position_marked",),
        )
        await self._store.save(opportunity, event)
        return event

    async def close_position(
        self,
        *,
        source_event_id: UUID,
        symbol: str,
        strategy_id: str,
        bid: Decimal,
        ask: Decimal,
        occurred_at: datetime,
        reason: IntradayCloseReason,
        exit_fee: Decimal = Decimal("0"),
    ) -> IntradayOpportunityEvent | None:
        """Close explicitly for a flow reversal or a supervised paper decision."""

        if await self._store.source_event_seen(source_event_id):
            return None
        _validate_quote_and_time(bid=bid, ask=ask, occurred_at=occurred_at)
        if exit_fee < 0:
            raise ValueError("exit fee cannot be negative")
        active = await self._store.load_active(symbol, strategy_id)
        if active is None or occurred_at < active.updated_at:
            return None
        mark_price = bid if active.side is IntradaySide.LONG else ask
        return await self._close(
            active,
            source_event_id=source_event_id,
            mark_price=mark_price,
            occurred_at=occurred_at,
            reason=reason,
            exit_fee=exit_fee,
        )

    async def _close(
        self,
        active: IntradayOpportunity,
        *,
        source_event_id: UUID,
        mark_price: Decimal,
        occurred_at: datetime,
        reason: IntradayCloseReason,
        exit_fee: Decimal,
    ) -> IntradayOpportunityEvent:
        marked = _mark(active, mark_price=mark_price, occurred_at=occurred_at)
        exit_action = (
            IntradayTradeAction.SELL
            if active.side is IntradaySide.LONG
            else IntradayTradeAction.BUY
        )
        fill = IntradayFill(
            fill_id=self._id_factory(),
            opportunity_id=active.opportunity_id,
            source_event_id=source_event_id,
            occurred_at=occurred_at,
            role=IntradayFillRole.EXIT,
            action=exit_action,
            quantity=active.quantity,
            price=mark_price,
            fee=exit_fee,
        )
        fees_total = active.entry_fill.fee + exit_fee
        net_pnl = marked.gross_pnl - fees_total
        opportunity = IntradayOpportunity.model_validate(
            {
                **marked.model_dump(mode="python"),
                "status": IntradayOpportunityStatus.CLOSED,
                "closed_at": occurred_at,
                "close_reason": reason,
                "exit_price": mark_price,
                "exit_fill": fill,
                "fees_total": fees_total,
                "net_pnl": net_pnl,
                "net_pnl_percent": _notional_percent(
                    net_pnl, active.entry_price * active.quantity
                ),
            }
        )
        event = IntradayOpportunityEvent(
            event_id=self._id_factory(),
            source_event_id=source_event_id,
            kind=IntradayOpportunityEventKind.CLOSED,
            occurred_at=occurred_at,
            opportunity=opportunity,
            reasons=(f"paper_position_closed:{reason.value.lower()}",),
            fill=fill,
        )
        await self._store.save(opportunity, event)
        return event


def _mark(
    active: IntradayOpportunity,
    *,
    mark_price: Decimal,
    occurred_at: datetime,
) -> IntradayOpportunity:
    gross_pnl, gross_percent = _returns(
        side=active.side,
        entry_price=active.entry_price,
        mark_price=mark_price,
        quantity=active.quantity,
    )
    net_pnl = gross_pnl - active.entry_fill.fee
    return IntradayOpportunity.model_validate(
        {
            **active.model_dump(mode="python"),
            "updated_at": occurred_at,
            "current_price": mark_price,
            "highest_mark": max(active.highest_mark, mark_price),
            "lowest_mark": min(active.lowest_mark, mark_price),
            "gross_pnl": gross_pnl,
            "net_pnl": net_pnl,
            "gross_pnl_percent": gross_percent,
            "net_pnl_percent": _notional_percent(
                net_pnl, active.entry_price * active.quantity
            ),
            "mfe_percent": max(active.mfe_percent, gross_percent, Decimal("0")),
            "mae_percent": min(active.mae_percent, gross_percent, Decimal("0")),
            "revision": active.revision + 1,
        }
    )


def _automatic_close_reason(
    active: IntradayOpportunity,
    *,
    mark_price: Decimal,
    occurred_at: datetime,
    end_of_day_cutoff: time,
) -> IntradayCloseReason | None:
    if active.side is IntradaySide.LONG:
        if mark_price <= active.stop_price:
            return IntradayCloseReason.STOP
        if mark_price >= active.target_price:
            return IntradayCloseReason.TARGET
    else:
        if mark_price >= active.stop_price:
            return IntradayCloseReason.STOP
        if mark_price <= active.target_price:
            return IntradayCloseReason.TARGET
    local_time = occurred_at.astimezone(_NEW_YORK)
    if local_time.time() >= end_of_day_cutoff:
        return IntradayCloseReason.END_OF_DAY
    if occurred_at >= active.expires_at:
        return IntradayCloseReason.TIME_EXIT
    return None


def _returns(
    *,
    side: IntradaySide,
    entry_price: Decimal,
    mark_price: Decimal,
    quantity: Decimal,
) -> tuple[Decimal, Decimal]:
    difference = (
        mark_price - entry_price
        if side is IntradaySide.LONG
        else entry_price - mark_price
    )
    pnl = difference * quantity
    return pnl, _notional_percent(pnl, entry_price * quantity)


def _notional_percent(value: Decimal, notional: Decimal) -> Decimal:
    return (value / notional * Decimal("100")).quantize(
        _FOUR_PLACES,
        rounding=ROUND_HALF_UP,
    )


def _validate_quote_and_time(*, bid: Decimal, ask: Decimal, occurred_at: datetime) -> None:
    if occurred_at.tzinfo is None or occurred_at.utcoffset() != UTC.utcoffset(occurred_at):
        raise ValueError("occurred_at must be timezone-aware UTC")
    if bid <= 0 or ask <= 0 or bid > ask:
        raise ValueError("quote must satisfy 0 < bid <= ask")
