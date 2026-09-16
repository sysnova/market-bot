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
        assert (
            options.replay_all
            if subject.startswith("marketbot.v1.analysis.result.")
            else options.replay_latest_per_subject
        )
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


async def test_short_context_uses_configured_alert_and_loses_freshness_with_connection() -> None:
    async def send(payload: dict[str, Any]) -> None:
        pass

    session = TickerWebSession(
        bus=Bus(),
        send=send,
        reviewer=None,
        engines={"swing": "active"},
        engine_versions={"alert": "3.10.0"},
        clock=Clock(),
    )
    try:
        await session.watch("NVDA")
        assert session.book is not None
        session.book.merge("analysis.result.produced", envelope().payload, received_at=NOW)
        current = session.snapshot()
        assert current["short_context"]["route"]["implementation"] == "3.10.0"
        assert current["short_context"]["structure"]["freshness"] == "FRESH"
        session._transport = "UNAVAILABLE"
        assert session.snapshot()["short_context"]["structure"]["freshness"] == "UNKNOWN"
        assert current["short_context"]["structure"]["freshness"] == "FRESH"
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


async def test_stop_cancels_analysis_and_question_unsubscribes_and_allows_restart(
    tmp_path: Path,
) -> None:
    sent = []
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def send(payload: dict[str, Any]) -> None:
        sent.append(payload)

    async def analyze(symbol: str) -> dict[str, object]:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
        return {}

    bus, reviewer = Bus(), Reviewer()
    session = TickerWebSession(
        bus=bus, send=send, reviewer=reviewer, engines={}, clock=Clock(),
        analyze=analyze, ledger_root=tmp_path,
    )
    try:
        await session.watch("ASTS")
        old_handler = bus.handlers[0]
        await old_handler(envelope("ASTS"))
        await session.handle({"type": "analyze_ticker", "symbol": "ASTS"})
        await session.handle({"type": "ask_ticker", "symbol": "ASTS", "question": "Short?"})
        await asyncio.wait_for(started.wait(), 1)
        await asyncio.wait_for(reviewer.called.wait(), 1)
        jobs = list(session._jobs.values())
        await session.handle({"type": "stop_ticker", "symbol": "ASTS", "request_id": "stop-1"})
        assert sent[-1] == {
            "type": "ticker_stopped", "symbol": "ASTS", "request_id": "stop-1",
        }
        assert cancelled.is_set()
        assert all(job.cancelled() for job in jobs)
        assert all(subscription.closed for subscription in bus.subscriptions)
        assert session.book is None
        assert session._pump_task is None
        assert session._jobs == {}
        await old_handler(envelope("ASTS"))
        assert session.book is None
        await session.handle({"type": "stop_ticker", "symbol": "ASTS"})
        assert sent[-1]["type"] == "ticker_stopped"
        await session.watch("ASTS")
        await old_handler(envelope("ASTS"))
        assert session.snapshot()["assessments"] == []
    finally:
        await session.close()


async def test_stop_for_an_old_ticker_does_not_stop_current_selection() -> None:
    sent = []

    async def send(payload: dict[str, Any]) -> None:
        sent.append(payload)

    session = TickerWebSession(bus=Bus(), send=send, reviewer=None, engines={})
    try:
        await session.watch("NBIS")
        await session.handle({"type": "stop_ticker", "symbol": "ASTS"})
        assert sent[-1]["type"] == "error"
        assert session.book is not None and session.book.symbol == "NBIS"
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
        assert sent[-1]["reconnect_enabled"] is False
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


async def test_new_session_restores_newest_analysis_even_after_older_bootstrap_publish() -> None:
    class ReplayBus(Bus):
        async def subscribe(self, subject: str, handler: Any, *, options: Any) -> Subscription:
            subscription = Subscription()
            self.subscriptions.append(subscription)
            if subject.startswith("marketbot.v1.analysis.result."):
                recent = envelope()
                old = envelope().model_copy(
                    update={
                        "payload": {
                            "symbol": "NVDA",
                            "engine_id": "swing",
                            "as_of": "2026-09-14T13:45:00Z",
                        }
                    }
                )
                for event in [recent, old] if options.replay_all else [old]:
                    await handler(event)
            return subscription

    async def send(payload: dict[str, Any]) -> None:
        pass

    session = TickerWebSession(bus=ReplayBus(), send=send, reviewer=None, engines={}, clock=Clock())
    try:
        await session.watch("NVDA")
        card = session.snapshot()["assessments"][0]
        assert card["as_of"] == NOW.isoformat()
        assert card["freshness"] == "FRESH"
    finally:
        await session.close()


async def test_slow_replay_keeps_subscription_and_eventually_becomes_live() -> None:
    class SlowBus(Bus):
        delayed = True

        async def wait_until_caught_up(self, subscription: Any, *, timeout_seconds: float) -> None:
            if self.delayed:
                raise TimeoutError("replay still in progress")

    async def send(payload: dict[str, Any]) -> None:
        pass

    bus = SlowBus()
    session = TickerWebSession(bus=bus, send=send, reviewer=None, engines={}, clock=Clock())
    try:
        await session.watch("NVDA")
        assert session.snapshot()["transport"] == "SYNCING"
        assert not any(item.closed for item in bus.subscriptions)
        await bus.handlers[0](envelope())
        assert session.snapshot()["assessments"][0]["freshness"] == "UNKNOWN"
        bus.delayed = False
        await session._refresh_transport()
        assert session.snapshot()["transport"] == "NATS_REPLAY_AND_LIVE"
        assert session.snapshot()["assessments"][0]["freshness"] == "FRESH"
        assert len(bus.subscriptions) == len(ticker_subjects("NVDA"))
    finally:
        await session.close()
    assert all(item.closed for item in bus.subscriptions)


async def test_subscription_failure_retries_and_restores_live_delivery() -> None:
    class FailingBus(Bus):
        fail = True

        async def subscribe(self, subject: str, handler: Any, *, options: Any) -> Subscription:
            if self.fail and self.subscriptions:
                raise ConnectionError("unavailable")
            return await super().subscribe(subject, handler, options=options)

    recovered = asyncio.Event()

    async def send(payload: dict[str, Any]) -> None:
        if payload["transport"] == "NATS_REPLAY_AND_LIVE":
            recovered.set()

    bus = FailingBus()
    session = TickerWebSession(bus=bus, send=send, reviewer=None, engines={}, clock=Clock())
    try:
        await session.watch("NVDA")
        assert session.snapshot()["transport"] == "UNAVAILABLE"
        assert all(item.closed for item in bus.subscriptions)
        bus.fail = False
        session._dirty.set()
        await asyncio.wait_for(recovered.wait(), timeout=2)
        await bus.handlers[-1](envelope())
        assert session.snapshot()["transport"] == "NATS_REPLAY_AND_LIVE"
        assert session.snapshot()["assessments"][0]["freshness"] == "FRESH"
    finally:
        await session.close()
