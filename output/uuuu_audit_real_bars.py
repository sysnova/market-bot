import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from app.contracts import BarTimeframe, MarketBar
from app.integration.bar_aggregator import RegularSessionFourHourAggregator
from app.integration.engine_assembly import MarketBotAssembly
from app.swing_4h_geri_engine.models import Swing4HGeriContext
from app.swing_4h_geri_engine.tactical_levels import completed_at

base = Path("output")
raw = json.loads((base / "uuuu-alpaca-15m-audit.json").read_text())
agg = RegularSessionFourHourAggregator()
bars = []
for record in raw["bars"]["UUUU"]:
    bar = MarketBar(
        symbol="UUUU",
        timeframe=BarTimeframe.MINUTE_15,
        timestamp=datetime.fromisoformat(record["timestamp"]),
        **{k: Decimal(str(record[k])) for k in ("open", "high", "low", "close", "volume", "vwap")},
        trade_count=record["trade_count"],
        source="alpaca",
        feed="sip",
        is_final=True,
    )
    bars.extend(agg.add(bar))
ny = ZoneInfo("America/New_York")


def short(b: MarketBar) -> dict[str, object]:
    return {
        "t": b.timestamp.isoformat(),
        "et": b.timestamp.astimezone(ny).strftime("%Y-%m-%d %H:%M"),
        **{
            k: float(getattr(b, v))
            for k, v in [
                ("o", "open"),
                ("h", "high"),
                ("l", "low"),
                ("c", "close"),
                ("v", "volume"),
            ]
        },
    }


out = {
    "source": "Alpaca SIP 15Min, aggregated with RegularSessionFourHourAggregator",
    "bars": [short(b) for b in bars],
}
for version, path in [("1.12.0", "7.56.0"), ("1.8.0", "7.54.0")]:
    engine = MarketBotAssembly.from_path(Path(f"configs/marketbot/{path}.yaml")).build_4hgeri()
    context = Swing4HGeriContext(
        symbol="UUUU",
        bars=tuple(bars[-60:]),
        current_price=bars[-1].close,
        as_of=completed_at(bars[-1]),
        current_price_at=completed_at(bars[-1]),
    )
    result = engine.analyze(context)
    out[version] = json.loads(result.model_dump_json())
    print(
        version,
        result.maturity,
        [(v.sequence, str(v.price), str(v.source_at)) for v in result.levels],
    )
matches = []
for name, price, field in [
    ("N1", "14.025", "low"),
    ("N2", "15.367", "high"),
    ("N3", "13.61", "low"),
]:
    hits = [short(b) for b in bars if getattr(b, field) == Decimal(price)]
    matches.append({"level": name, "price": price, "matches": hits})
out["reported_matches"] = matches
print("matches", json.dumps(matches))
print("last", json.dumps(out["bars"][-1]))
(base / "uuuu-geri-real-audit.json").write_text(json.dumps(out, ensure_ascii=False))
