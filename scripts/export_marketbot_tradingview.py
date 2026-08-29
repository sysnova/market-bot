"""Calculate MarketBot price geometry from Alpaca and export it for TradingView."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from typing import cast

from app.integration.tradingview_projection import (
    TRADINGVIEW_COLUMNS,
    calculate_tradingview_assessments,
    project_tradingview_row,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Calculate MarketBot Swing, Fibonacci, AVWAP and 4H geometry directly "
            "from Alpaca, without JetStream."
        )
    )
    parser.add_argument("ticker", nargs="+", help="One or more US equity/ETF symbols")
    parser.add_argument(
        "--format",
        choices=("csv", "json"),
        default="csv",
        help="Output format (default: csv)",
    )
    parser.add_argument(
        "--no-header",
        action="store_true",
        help="Omit the CSV header when appending to an existing TradingView array",
    )
    return parser.parse_args()


async def run(args: argparse.Namespace) -> None:
    tickers = cast(list[str], args.ticker)
    assessments = await calculate_tradingview_assessments(tickers)
    rows = [project_tradingview_row(item) for item in assessments]
    for item in assessments:
        for layer, reason in item.errors.items():
            print(f"WARNING {item.symbol} {layer}: {reason}", file=sys.stderr)
    if args.format == "json":
        print(json.dumps(rows[0] if len(rows) == 1 else rows, indent=2, ensure_ascii=False))
        return

    writer = csv.writer(sys.stdout, lineterminator="\n")
    if not args.no_header:
        writer.writerow(TRADINGVIEW_COLUMNS)
    for row in rows:
        writer.writerow(row[column] for column in TRADINGVIEW_COLUMNS)


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
