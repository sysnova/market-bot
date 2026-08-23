"""Local PostgreSQL adapter for independent intraday paper round trips."""

from __future__ import annotations

import json
from datetime import date
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.contracts import IntradayOpportunity, IntradayOpportunityEvent


class PostgresIntradayOpportunityStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def is_ready(self) -> bool:
        async with self._session_factory() as session:
            tables = await session.execute(
                text(
                    """
                    select
                      to_regclass('market_bot.intraday_opportunities'),
                      to_regclass('market_bot.intraday_opportunity_events'),
                      to_regclass('market_bot.intraday_fills')
                    """
                )
            )
            row = tables.one()
            return all(value is not None for value in row)

    async def load_active(
        self, symbol: str, strategy_id: str
    ) -> IntradayOpportunity | None:
        async with self._session_factory() as session:
            payload = await session.scalar(
                text(
                    """
                    select payload
                    from market_bot.intraday_opportunities
                    where symbol = :symbol and strategy_id = :strategy_id and status = 'OPEN'
                    order by updated_at desc
                    limit 1
                    """
                ),
                {
                    "symbol": symbol.strip().upper(),
                    "strategy_id": strategy_id.strip(),
                },
            )
        return (
            IntradayOpportunity.model_validate(payload, strict=False)
            if payload is not None
            else None
        )

    async def list_session(self, session_date: date) -> tuple[IntradayOpportunity, ...]:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    text(
                        """
                        select payload
                        from market_bot.intraday_opportunities
                        where session_date = :session_date
                        order by opened_at, symbol, strategy_id
                        """
                    ),
                    {"session_date": session_date},
                )
            ).scalars()
            return tuple(
                IntradayOpportunity.model_validate(payload, strict=False)
                for payload in rows
            )

    async def source_event_seen(self, source_event_id: UUID) -> bool:
        async with self._session_factory() as session:
            value = await session.scalar(
                text(
                    """
                    select exists(
                      select 1 from market_bot.intraday_opportunity_events
                      where source_event_id = :source_event_id
                    )
                    """
                ),
                {"source_event_id": source_event_id},
            )
            return bool(value)

    async def save(
        self,
        opportunity: IntradayOpportunity,
        event: IntradayOpportunityEvent,
    ) -> None:
        payload = opportunity.model_dump(mode="json")
        async with self._session_factory() as session, session.begin():
            duplicate = await session.scalar(
                text(
                    """
                    select exists(
                      select 1 from market_bot.intraday_opportunity_events
                      where source_event_id = :source_event_id
                    )
                    """
                ),
                {"source_event_id": event.source_event_id},
            )
            if duplicate:
                return
            await session.execute(
                text(
                    """
                    insert into market_bot.intraday_opportunities (
                      id, symbol, strategy_id, session_date, side, status,
                      opened_at, updated_at, expires_at, closed_at, close_reason,
                      quantity, entry_price, current_price, exit_price, stop_price,
                      target_price, highest_mark, lowest_mark, gross_pnl, net_pnl,
                      gross_pnl_percent, net_pnl_percent, mfe_percent, mae_percent,
                      fees_total, revision, source_signal_id, payload, created_at
                    ) values (
                      :id, :symbol, :strategy_id, :session_date, :side, :status,
                      :opened_at, :updated_at, :expires_at, :closed_at, :close_reason,
                      :quantity, :entry_price, :current_price, :exit_price, :stop_price,
                      :target_price, :highest_mark, :lowest_mark, :gross_pnl, :net_pnl,
                      :gross_pnl_percent, :net_pnl_percent, :mfe_percent, :mae_percent,
                      :fees_total, :revision, :source_signal_id, cast(:payload as jsonb),
                      :created_at
                    )
                    on conflict (id) do update set
                      status = excluded.status,
                      updated_at = excluded.updated_at,
                      closed_at = excluded.closed_at,
                      close_reason = excluded.close_reason,
                      current_price = excluded.current_price,
                      exit_price = excluded.exit_price,
                      highest_mark = excluded.highest_mark,
                      lowest_mark = excluded.lowest_mark,
                      gross_pnl = excluded.gross_pnl,
                      net_pnl = excluded.net_pnl,
                      gross_pnl_percent = excluded.gross_pnl_percent,
                      net_pnl_percent = excluded.net_pnl_percent,
                      mfe_percent = excluded.mfe_percent,
                      mae_percent = excluded.mae_percent,
                      fees_total = excluded.fees_total,
                      revision = excluded.revision,
                      payload = excluded.payload
                    where market_bot.intraday_opportunities.revision < excluded.revision
                    """
                ),
                {
                    "id": opportunity.opportunity_id,
                    "symbol": opportunity.symbol,
                    "strategy_id": opportunity.strategy_id,
                    "session_date": opportunity.session_date,
                    "side": opportunity.side.value,
                    "status": opportunity.status.value,
                    "opened_at": opportunity.opened_at,
                    "updated_at": opportunity.updated_at,
                    "expires_at": opportunity.expires_at,
                    "closed_at": opportunity.closed_at,
                    "close_reason": (
                        opportunity.close_reason.value
                        if opportunity.close_reason is not None
                        else None
                    ),
                    "quantity": opportunity.quantity,
                    "entry_price": opportunity.entry_price,
                    "current_price": opportunity.current_price,
                    "exit_price": opportunity.exit_price,
                    "stop_price": opportunity.stop_price,
                    "target_price": opportunity.target_price,
                    "highest_mark": opportunity.highest_mark,
                    "lowest_mark": opportunity.lowest_mark,
                    "gross_pnl": opportunity.gross_pnl,
                    "net_pnl": opportunity.net_pnl,
                    "gross_pnl_percent": opportunity.gross_pnl_percent,
                    "net_pnl_percent": opportunity.net_pnl_percent,
                    "mfe_percent": opportunity.mfe_percent,
                    "mae_percent": opportunity.mae_percent,
                    "fees_total": opportunity.fees_total,
                    "revision": opportunity.revision,
                    "source_signal_id": opportunity.source_signal_id,
                    "payload": json.dumps(payload, separators=(",", ":")),
                    "created_at": opportunity.opened_at,
                },
            )
            if event.fill is not None:
                fill = event.fill
                await session.execute(
                    text(
                        """
                        insert into market_bot.intraday_fills (
                          id, opportunity_id, source_event_id, occurred_at, role,
                          action, quantity, price, fee, payload, created_at
                        ) values (
                          :id, :opportunity_id, :source_event_id, :occurred_at, :role,
                          :action, :quantity, :price, :fee, cast(:payload as jsonb), :created_at
                        ) on conflict (source_event_id) do nothing
                        """
                    ),
                    {
                        "id": fill.fill_id,
                        "opportunity_id": fill.opportunity_id,
                        "source_event_id": fill.source_event_id,
                        "occurred_at": fill.occurred_at,
                        "role": fill.role.value,
                        "action": fill.action.value,
                        "quantity": fill.quantity,
                        "price": fill.price,
                        "fee": fill.fee,
                        "payload": json.dumps(
                            fill.model_dump(mode="json"), separators=(",", ":")
                        ),
                        "created_at": fill.occurred_at,
                    },
                )
            await session.execute(
                text(
                    """
                    insert into market_bot.intraday_opportunity_events (
                      id, source_event_id, opportunity_id, symbol, strategy_id,
                      session_date, kind, occurred_at, reasons, payload, created_at
                    ) values (
                      :id, :source_event_id, :opportunity_id, :symbol, :strategy_id,
                      :session_date, :kind, :occurred_at, cast(:reasons as jsonb),
                      cast(:payload as jsonb), :created_at
                    ) on conflict (source_event_id) do nothing
                    """
                ),
                {
                    "id": event.event_id,
                    "source_event_id": event.source_event_id,
                    "opportunity_id": opportunity.opportunity_id,
                    "symbol": opportunity.symbol,
                    "strategy_id": opportunity.strategy_id,
                    "session_date": opportunity.session_date,
                    "kind": event.kind.value,
                    "occurred_at": event.occurred_at,
                    "reasons": json.dumps(list(event.reasons), separators=(",", ":")),
                    "payload": json.dumps(
                        event.model_dump(mode="json"), separators=(",", ":")
                    ),
                    "created_at": event.occurred_at,
                },
            )
