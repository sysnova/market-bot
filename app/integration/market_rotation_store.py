"""PostgreSQL persistence for the independent rotation process."""

# ruff: noqa: E501 -- SQL statements retain their column/value alignment.

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.market_rotation_engine import SectorProfile


class PostgresMarketRotationStore:
    def __init__(self, engine: AsyncEngine, *, customer_slug: str = "stock-analyzer") -> None:
        self._engine = engine
        self._slug = customer_slug

    async def load_profiles(self) -> tuple[SectorProfile, ...]:
        async with self._engine.connect() as connection:
            rows = (
                await connection.execute(
                    text("""
                with latest as (
                  select r.id from stock.market_rotation_run r
                  join stock.customer c on c.id=r.customer_id
                  where c.slug=:slug order by r.generated_at desc limit 1
                )
                select s.sector_code, s.sector_label, s.proxy_symbol, s.benchmark_symbol,
                       array_agg(e.symbol order by e.symbol) filter (where e.role='basket') symbols
                from stock.market_rotation_sector_snapshot s
                join latest l on l.id=s.run_id
                join stock.market_rotation_symbol_evidence e on e.sector_snapshot_id=s.id
                group by s.sector_code,s.sector_label,s.proxy_symbol,s.benchmark_symbol
                order by s.sector_code
            """),
                    {"slug": self._slug},
                )
            ).all()
        if not rows:
            raise RuntimeError("No hay perfiles de rotación migrados en PostgreSQL local")
        return tuple(
            SectorProfile(
                str(r.sector_code),
                str(r.sector_label),
                str(r.proxy_symbol),
                str(r.benchmark_symbol),
                tuple(r.symbols or ()),
            )
            for r in rows
        )

    async def save(
        self, results: tuple[dict[str, object], ...], *, generated_at: datetime
    ) -> tuple[str, tuple[str, ...]]:
        run_id = str(uuid4())
        additions: list[str] = []
        async with self._engine.begin() as connection:
            customer_id = (
                await connection.execute(
                    text("select id from stock.customer where slug=:slug"), {"slug": self._slug}
                )
            ).scalar_one()
            await connection.execute(
                text("""insert into stock.market_rotation_run
              (id,customer_id,run_key,engine_version,status,generated_at,started_at,completed_at,lookback_days,benchmark_symbols,sector_count,risk_regime,summary_message,metadata_json)
              values (:id,:customer,:key,'marketbot-1.0.0','completed',:at,:at,:at,90,array['SPY','QQQ','IWM'],:count,'neutral',:summary,cast(:metadata as jsonb))"""),
                {
                    "id": run_id,
                    "customer": customer_id,
                    "key": f"marketbot-{generated_at.strftime('%Y%m%dT%H%M%S%fZ')}",
                    "at": generated_at,
                    "count": len(results),
                    "summary": "MarketBot local market rotation analysis",
                    "metadata": json.dumps({"source": "marketbot-local"}),
                },
            )
            for sector in results:
                snapshot_id = str(uuid4())
                evidence = sector["evidence"]
                top = tuple(str(item["symbol"]) for item in evidence[:5])
                await connection.execute(
                    text("""insert into stock.market_rotation_sector_snapshot
                  (id,customer_id,run_id,sector_code,sector_label,proxy_symbol,benchmark_symbol,rotation_score,rotation_state,relative_return_20d_percent,dollar_volume_rvol,breadth_positive_percent,breadth_above_sma20_percent,top_symbols)
                  values (:id,:customer,:run,:code,:label,:proxy,:benchmark,:score,:state,:relative,:rvol,:positive,:above,:top)"""),
                    {
                        "id": snapshot_id,
                        "customer": customer_id,
                        "run": run_id,
                        "code": sector["code"],
                        "label": sector["label"],
                        "proxy": sector["proxy"],
                        "benchmark": sector["benchmark"],
                        "score": sector["score"],
                        "state": sector["state"],
                        "relative": sector["relative_20d"],
                        "rvol": sector["rvol"],
                        "positive": sector["breadth_positive"],
                        "above": sector["breadth_above_sma20"],
                        "top": list(top),
                    },
                )
                for item in evidence:
                    await connection.execute(
                        text("""insert into stock.market_rotation_symbol_evidence
                      (customer_id,sector_snapshot_id,sector_code,symbol,role,price,return_1d_percent,return_5d_percent,return_20d_percent,dollar_volume,average_dollar_volume_20,dollar_volume_rvol,above_sma20,above_sma50,symbol_score)
                      values (:customer,:snapshot,:sector,:symbol,'basket',:price,:r1,:r5,:r20,:dv,:avg,:rvol,:sma20,:sma50,:score)"""),
                        {
                            "customer": customer_id,
                            "snapshot": snapshot_id,
                            "sector": sector["code"],
                            "symbol": item["symbol"],
                            "price": item["price"],
                            "r1": item["return_1d"],
                            "r5": item["return_5d"],
                            "r20": item["return_20d"],
                            "dv": item["dollar_volume"],
                            "avg": item["average_dollar_volume_20"],
                            "rvol": item["rvol"],
                            "sma20": item["above_sma20"],
                            "sma50": item["above_sma50"],
                            "score": item["score"],
                        },
                    )
                if sector["state"] in {"INFLOW", "ACCUMULATING"}:
                    additions.extend(
                        str(item["symbol"])
                        for item in evidence[:3]
                        if item["score"] >= Decimal("60")
                    )
            unique = tuple(dict.fromkeys(additions))
            for symbol in unique:
                await connection.execute(
                    text("""insert into stock.watchlist_symbol (watchlist_id,symbol,status,notes,metadata_json)
                  select w.id,:symbol,'active','ROT: agregado por MarketBot',cast(:metadata as jsonb) from stock.watchlist w where w.customer_id=:customer and w.code='default'
                  on conflict (watchlist_id,symbol) do update set status='active',notes='ROT: actualizado por MarketBot',metadata_json=excluded.metadata_json,updated_at=now()"""),
                    {
                        "symbol": symbol,
                        "customer": customer_id,
                        "metadata": json.dumps({"source": "ROT", "rotation_run_id": run_id}),
                    },
                )
        return run_id, tuple(dict.fromkeys(additions))
