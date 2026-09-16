"""Synthetic retained-memory comparison; no PostgreSQL, NATS or market providers.

Run: uv run python scripts/benchmark_shared_ticker_cache.py
Each mode runs in a fresh child; shared RSS includes its in-process cache server.
"""

from __future__ import annotations

import gc
import json
import subprocess
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter

import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def measure(mode: str) -> dict[str, object]:
    from app.contracts import BarTimeframe, MarketBar
    from app.integration import ticker_cache_transport
    from app.integration.market_bar_store import MarketBarStore
    from app.integration.ticker_cache_transport import CacheClient, make_cache_server

    process = psutil.Process()
    server = None
    client = None
    thread = None
    baseline = process.memory_info().rss
    if mode == "shared":
        server = make_cache_server("synthetic-benchmark")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        client = CacheClient(server.server_port, "synthetic-benchmark")
        ticker_cache_transport._client = client
    start = perf_counter()
    stores = [MarketBarStore(capacity_per_series=260) for _ in range(4)]
    at = datetime(2025, 1, 1, tzinfo=UTC)
    for store in stores:
        for symbol_index in range(206):
            symbol = f"BENCH{symbol_index}"
            for index in range(260):
                # Fresh objects simulate independent PostgreSQL/NATS deserialization.
                payload = {
                    "symbol": symbol, "timeframe": "1Day",
                    "timestamp": (at + timedelta(days=index)).isoformat(),
                    "open": "100", "high": "105", "low": "99", "close": "103",
                    "volume": "50000", "source": "benchmark", "feed": "sip",
                }
                store.add(MarketBar.model_validate_json(json.dumps(payload)))
            assert len(store.history(symbol, BarTimeframe.DAY_1)) == 260
    del payload
    gc.collect()
    result: dict[str, object] = {
        "mode": mode, "symbols": 206, "bars_per_symbol": 260, "engines": 4,
        "retained_rss_delta_mib": round((process.memory_info().rss - baseline) / 2**20, 2),
        "load_seconds": round(perf_counter() - start, 3),
    }
    read_start = perf_counter()
    for store in stores:
        for symbol_index in range(206):
            assert len(store.history(f"BENCH{symbol_index}", BarTimeframe.DAY_1)) == 260
    result["read_824_windows_seconds"] = round(perf_counter() - read_start, 3)
    if client is not None:
        result["cache"] = client.call("stats")
        client.close()
    if server is not None:
        server.shutdown()
        server.server_close()
    if thread is not None:
        thread.join()
    return result


if __name__ == "__main__":
    if len(sys.argv) > 1:
        print(json.dumps(measure(sys.argv[1])))
    else:
        results = []
        for mode in ("local", "shared"):
            result = subprocess.run(  # noqa: S603 - this benchmark's own isolated child.
                [sys.executable, str(Path(__file__).resolve()), mode],
                cwd=ROOT, capture_output=True, text=True, check=True, timeout=180,
            )
            results.append(json.loads(result.stdout))
        print(json.dumps(results, indent=2))
