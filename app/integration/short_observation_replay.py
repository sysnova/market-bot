"""Isolated multi-session SHORT research; never synthesizes quotes from OHLCV."""

import argparse
import asyncio
import json
from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from app.common.clock import FrozenClock
from app.common.settings import AppSettings
from app.contracts import (
    ANALYSIS_RESULT_EVENT,
    MARKET_BAR_EVENT,
    SHORT_OBSERVATION_EVENT,
    SUPPORT_ASSESSMENT_EVENT,
    AnalysisResult,
    EventEnvelope,
    MarketBar,
    ShortObservation,
)
from app.leveraged_thesis_engine.v14 import LeveragedThesisEngineV14

from .engine_assembly import MarketBotAssembly
from .intraday_worker import IntradayWorker
from .short_observation_runtime import ShortObservationRuntime
from .signal_backtest import (
    SignalBacktestConfig,
    _build_read_only_rest,  # pyright: ignore[reportPrivateUsage]
    load_backtest_market_data,
)
from .support_confirmation_composition import SupportConfirmationRuntime
from .swing_worker import SwingWorker

_NY = ZoneInfo("America/New_York")


def hypothetical_short_outcome(
    bars: tuple[MarketBar, ...],
    *,
    available_at: datetime,
    stop: Decimal,
    target: Decimal,
) -> dict[str, object]:
    """Independent next-minute-open sensitivity, not an ETF fill or executed trade."""
    future = sorted((b for b in bars if b.timestamp >= available_at), key=lambda b: b.timestamp)
    if not future:
        return {"first_level": "NO_FUTURE_BARS", "return_percent": None}
    entry = future[0].open
    base: dict[str, object] = {
        "entry": str(entry),
        "entry_time": future[0].timestamp.isoformat(),
        "stop": str(stop),
        "target": str(target),
        "return_percent": None,
        "assumption": "next minute open; underlying only; excludes costs and quote validity",
    }
    if not stop > entry > target:
        return base | {"first_level": "NOT_ENTERABLE"}
    base["remaining_rr"] = str((entry - target) / (stop - entry))
    for bar in future:
        stopped, hit = bar.high >= stop, bar.low <= target
        if stopped or hit:
            outcome = "AMBIGUOUS_BOTH" if stopped and hit else "STOP" if stopped else "TARGET"
            result = (
                None if stopped and hit else (entry - (stop if stopped else target)) / entry * 100
            )
            return base | {
                "first_level": outcome,
                "exit_bar": bar.timestamp.isoformat(),
                "return_percent": str(result) if result is not None else None,
            }
    return base | {
        "first_level": "SESSION_CLOSE",
        "return_percent": str((entry - future[-1].close) / entry * 100),
    }


class _ReplayPublisher:
    def __init__(self, clock: FrozenClock, symbol: str) -> None:
        self.clock, self.symbol = clock, symbol
        self.observer: ShortObservationRuntime | None = None
        self.swing: SwingWorker | None = None
        self.mature: list[tuple[datetime, AnalysisResult]] = []
        self.reports: list[ShortObservation] = []

    async def publish(self, subject: str, envelope: EventEnvelope) -> None:
        del subject
        if envelope.event_type == SHORT_OBSERVATION_EVENT:
            self.reports.append(ShortObservation.model_validate(envelope.payload, strict=False))
            return
        if envelope.event_type == ANALYSIS_RESULT_EVENT:
            a = AnalysisResult.model_validate(envelope.payload, strict=False)
            m = {x.name: x.value for x in a.metrics}
            if (
                a.symbol == self.symbol
                and a.as_of.astimezone(_NY).date() == self.clock.now().astimezone(_NY).date()
                and a.as_of + timedelta(minutes=1) <= self.clock.now()
                and m.get("short_mature_confirmation_gate_passed") is True
            ):
                self.mature.append((self.clock.now(), a))
        if envelope.event_type == SUPPORT_ASSESSMENT_EVENT and self.swing is not None:
            await self.swing.handle_support_event(envelope)
        if self.observer is not None:
            await self.observer.handle(envelope)


async def replay_short_session(
    *,
    assembly: MarketBotAssembly,
    symbol: str,
    day: date,
    warmup: tuple[MarketBar, ...],
    session: tuple[MarketBar, ...],
) -> dict[str, object]:
    engine = assembly.build_leveraged_thesis()
    if not isinstance(engine, LeveragedThesisEngineV14):
        raise ValueError("definition must select the observation implementation")
    pair = engine.pair_for_underlying(symbol)
    if pair is None:
        raise ValueError("symbol is outside leveraged scope")
    clock = FrozenClock(datetime.combine(day, time(9, 30), _NY).astimezone(UTC))
    publisher = _ReplayPublisher(clock, symbol)
    observer = ShortObservationRuntime(engine, publisher, clock=clock)
    publisher.observer = observer
    swing = SwingWorker(publisher=publisher, analyzer=assembly.build_swing())
    publisher.swing = swing
    intraday = IntradayWorker(publisher=publisher, analyzer=assembly.build_intraday())
    support = SupportConfirmationRuntime(
        engine=assembly.build_support_confirmation(), publisher=publisher, clock=clock
    )
    swing.activate_universe((symbol,))
    intraday.activate_universe((symbol, pair.bearish_instrument))
    await support.bootstrap(warmup, symbols=(symbol,))
    await swing.bootstrap(warmup, symbols=(symbol,))
    await intraday.bootstrap(warmup, symbols=(symbol, pair.bearish_instrument))
    for b in sorted(session, key=lambda b: (b.timestamp, b.symbol)):
        available = b.timestamp + timedelta(minutes=1)
        if available > clock.now():
            clock.advance(available - clock.now())
        envelope = EventEnvelope(
            event_type=MARKET_BAR_EVENT,
            occurred_at=available,
            source="short-observation-replay",
            subject=b.symbol,
            payload=b,
        )
        if b.symbol == symbol:
            await swing.handle_market_event(envelope)
        await intraday.handle_market_event(envelope)
    close = datetime.combine(day, time(16), _NY).astimezone(UTC)
    if clock.now() < close:
        clock.advance(close - clock.now())
    await observer.tick()
    underlying_bars = tuple(b for b in session if b.symbol == symbol)
    candidates: list[dict[str, object]] = []
    seen: set[str] = set()
    for report in publisher.reports:
        if report.route != "SHORT_TACTICAL" or not report.setup_id or report.setup_id in seen:
            continue
        seen.add(report.setup_id)
        m = {x.name: x.value for x in report.metrics}
        candidates.append(
            {
                "setup_id": report.setup_id,
                "available_at": report.occurred_at.isoformat(),
                "outcome": hypothetical_short_outcome(
                    underlying_bars,
                    available_at=report.occurred_at,
                    stop=Decimal(str(m["invalidation"])),
                    target=Decimal(str(m["objective"])),
                ),
                "failed_gates": [g.reason for g in report.gates if not g.passed],
            }
        )
    return {
        "date": day.isoformat(),
        "symbol": symbol,
        "definition": assembly.definition.version,
        "underlying_minutes": len(underlying_bars),
        "instrument_minutes": sum(b.symbol == pair.bearish_instrument for b in session),
        "mature_timing_count": len(publisher.mature),
        "unique_tactical_intents": len(seen),
        "daily_ready_observations": sum(
            r.route == "SHORT_DAILY" and r.status == "READY" for r in publisher.reports
        ),
        "tactical_ready_observations": sum(
            r.route == "SHORT_TACTICAL" and r.status == "READY" for r in publisher.reports
        ),
        "quote_coverage": "unavailable: OHLCV is not executable quote evidence",
        "candidates": candidates,
        "mature_timing": [
            {"available_at": at.isoformat(), "analysis": a.model_dump(mode="json")}
            for at, a in publisher.mature
        ],
        "gate_failures": dict(
            Counter(
                g.reason
                for r in publisher.reports
                if r.route == "SHORT_TACTICAL"
                for g in r.gates
                if not g.passed
            )
        ),
        "observations": [r.model_dump(mode="json") for r in publisher.reports],
    }


def _unshift(bar: MarketBar) -> MarketBar:
    local = bar.timestamp.astimezone(_NY)
    original = datetime.combine(local.date() - timedelta(days=7), local.timetz()).astimezone(UTC)
    return bar.model_copy(update={"timestamp": original})


async def _main(args: argparse.Namespace) -> None:
    settings = AppSettings()
    assembly = MarketBotAssembly.from_path(Path(args.definition))
    symbol = str(args.symbol).strip().upper()
    pair = assembly.build_leveraged_thesis().pair_for_underlying(symbol)
    if pair is None:
        raise ValueError("unsupported underlying")
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    if start > end:
        raise ValueError("start must precede end")
    output = Path(args.output)
    await asyncio.to_thread(output.mkdir, parents=True, exist_ok=True)
    rest = _build_read_only_rest(settings)
    summaries: list[dict[str, object]] = []
    try:
        day = start
        while day <= end:
            if day.weekday() < 5:
                cached = output / f"{symbol}-{day}-bars.json"
                try:
                    if cached.exists():
                        raw = json.loads(cached.read_text(encoding="utf-8"))
                        warmup = tuple(
                            MarketBar.model_validate(b, strict=False) for b in raw["warmup"]
                        )
                        session = tuple(
                            MarketBar.model_validate(b, strict=False) for b in raw["session"]
                        )
                    else:
                        data = await load_backtest_market_data(
                            rest,
                            config=SignalBacktestConfig(
                                source_date=day,
                                simulated_date=day + timedelta(days=7),
                                symbols=(symbol, pair.bearish_instrument),
                            ),
                            feed=settings.alpaca_data_feed,
                        )
                        warmup, session = (
                            tuple(map(_unshift, data.warmup_bars)),
                            tuple(map(_unshift, data.session_bars)),
                        )
                        cached.write_text(
                            json.dumps(
                                {
                                    "warmup": [b.model_dump(mode="json") for b in warmup],
                                    "session": [b.model_dump(mode="json") for b in session],
                                }
                            ),
                            encoding="utf-8",
                        )
                    result = await replay_short_session(
                        assembly=assembly, symbol=symbol, day=day, warmup=warmup, session=session
                    )
                    (output / f"{symbol}-{day}-result.json").write_text(
                        json.dumps(result, indent=2), encoding="utf-8"
                    )
                    summaries.append(
                        {
                            k: v
                            for k, v in result.items()
                            if k not in {"observations", "mature_timing"}
                        }
                    )
                    print(
                        day,
                        "minutes",
                        result["underlying_minutes"],
                        "mature",
                        result["mature_timing_count"],
                        "intents",
                        result["unique_tactical_intents"],
                        flush=True,
                    )
                except RuntimeError as error:
                    if not str(error).startswith("no regular-session minute bars"):
                        raise
                    summaries.append({"date": day.isoformat(), "status": "NO_SESSION"})
                    print(day, "NO_SESSION", flush=True)
            day += timedelta(days=1)
    finally:
        await rest.close()
    (output / "summary.json").write_text(json.dumps(summaries, indent=2), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--definition", required=True)
    parser.add_argument("--output", required=True)
    asyncio.run(_main(parser.parse_args()))
