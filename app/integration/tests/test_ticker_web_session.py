import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.contracts import EventEnvelope
from app.integration.ticker_web_session import TickerWebSession, ticker_subjects

NOW = datetime(2026, 9, 14, 14, 30, tzinfo=UTC)


class Clock:
    def now(self) -> datetime:
        return NOW


class Subscription:
    closed = False

    async def unsubscribe(self) -> None:
        self.closed = True


class Bus:
    def __init__(self) -> None:
        self.handlers = []
        self.subscriptions = []

    async def subscribe(self, subject: str, handler: Any, *, options: Any) -> Subscription:
        assert options.replay_latest_per_subject
        self.handlers.append(handler)
        subscription = Subscription()
        self.subscriptions.append(subscription)
        return subscription

    async def wait_until_caught_up(self, subscription: Any, *, timeout_seconds: float) -> None:
        pass

    async def get_last(self, subject: str) -> None:
        return None


class Reviewer:
    model = "test-gpt"

    def __init__(self) -> None:
        self.called = asyncio.Event()
        self.release = asyncio.Event()
        self.dossiers = []

    async def ask_ticker(self, dossier: Any, *, question: str, history: Any) -> str:
        self.dossiers.append(dossier)
        self.called.set()
        await self.release.wait()
        return "Respuesta de prueba"


async def test_instrument_watch_receives_underlying_subject_without_mixing_other_pairs() -> None:
    assert "marketbot.v1.leveraged-thesis.assessment.*" in ticker_subjects("ASTN")
    sent = []

    async def send(payload: dict[str, Any]) -> None:
        sent.append(payload)

    bus = Bus()
    session = TickerWebSession(
        bus=bus, send=send, reviewer=None, engines={"leveraged-thesis": "active"}, clock=Clock()
    )
    try:
        await session.watch("ASTN")
        await bus.handlers[0](
            EventEnvelope(
                source="leveraged-thesis",
                event_type="leveraged-thesis.assessed",
                payload={
                    "underlying_symbol": "ASTS",
                    "instrument_symbol": "ASTN",
                    "occurred_at": NOW.isoformat(),
                },
            )
        )
        assert session.snapshot()["missing_engines"] == []
        assert session.snapshot()["assessments"][0]["engine"] == "leveraged-thesis"
    finally:
        await session.close()


def envelope(symbol: str = "NVDA", passed: bool = True) -> EventEnvelope:
    return EventEnvelope(
        source="swing",
        event_type="analysis.result.produced",
        payload={
            "symbol": symbol,
            "engine_id": "swing",
            "as_of": NOW.isoformat(),
            "metrics": [{"name": "entry_gate", "value": passed}],
        },
    )


async def test_question_freezes_context_while_live_data_keeps_changing(tmp_path: Path) -> None:
    sent = []

    async def send(payload: dict[str, Any]) -> None:
        sent.append(payload)

    bus, reviewer = Bus(), Reviewer()
    session = TickerWebSession(
        bus=bus,
        send=send,
        reviewer=reviewer,
        engines={"swing": "active"},
        clock=Clock(),
        ledger_root=tmp_path,
    )
    try:
        await session.watch("NVDA")
        await bus.handlers[0](envelope())
        await session.handle(
            {"type": "ask_ticker", "symbol": "NVDA", "request_id": "q1", "question": "¿Qué falta?"}
        )
        await asyncio.wait_for(reviewer.called.wait(), 1)
        await bus.handlers[0](envelope(passed=False))
        assert reviewer.dossiers[0]["assessments"][0]["gates"][0]["value"] is True
        assert session.snapshot()["assessments"][0]["gates"][0]["value"] is False
        reviewer.release.set()
        await session._jobs["ask_ticker"]
        assert sent[-1]["type"] == "ticker_answer"
        assert sent[-1]["request_id"] == "q1"
        assert await asyncio.to_thread(lambda: list(tmp_path.glob("*.ndjson")))
    finally:
        await session.close()


async def test_switch_unsubscribes_and_ignores_previous_ticker_callback() -> None:
    async def send(payload: dict[str, Any]) -> None:
        pass

    bus = Bus()
    session = TickerWebSession(bus=bus, send=send, reviewer=None, engines={}, clock=Clock())
    try:
        await session.watch("NVDA")
        previous = list(bus.subscriptions)
        old_handler = bus.handlers[0]
        await session.watch("AAPL")
        await old_handler(envelope())
        assert all(item.closed for item in previous)
        assert session.snapshot()["assessments"] == []
        assert session.snapshot()["symbol"] == "AAPL"
    finally:
        await session.close()


async def test_missing_bus_and_wrong_symbol_do_not_invoke_gpt() -> None:
    sent = []

    async def send(payload: dict[str, Any]) -> None:
        sent.append(payload)

    reviewer = Reviewer()
    session = TickerWebSession(bus=None, send=send, reviewer=reviewer, engines={})
    try:
        await session.watch("NVDA")
        assert sent[-1]["transport"] == "UNAVAILABLE"
        await session.handle({"type": "ask_ticker", "symbol": "AAPL", "question": "Hola"})
        await session.handle({"type": "ask_ticker", "symbol": "NVDA", "question": "Hola"})
        assert sent[-1]["type"] == "error"
        assert not reviewer.called.is_set()
    finally:
        await session.close()


def test_subjects_escape_share_class_and_never_subscribe_to_all_market_data() -> None:
    subjects = ticker_subjects("brk.b")
    assert "marketbot.v1.analysis.result.*.BRK_B" in subjects
    assert not any("market.bar" in item or item.endswith(">") for item in subjects)
