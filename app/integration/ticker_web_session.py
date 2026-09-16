"""Per-browser ticker subscriptions and explicit GPT requests for Opportunities."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast

from app.common.clock import SystemClock
from app.contracts import EntryOpportunity, EventEnvelope, Subscription, SubscriptionOptions
from app.event_bus import NatsJetStreamEventBus
from app.opportunity_dashboard.failure_review import FailureReviewError, OpenAIFailureReviewer
from app.opportunity_dashboard.short_context import build_short_context
from app.opportunity_dashboard.ticker_watch import TickerEvidenceBook, normalize_symbol

Send = Callable[[dict[str, Any]], Awaitable[None]]
Analyze = Callable[[str], Awaitable[dict[str, object]]]


def ticker_subjects(symbol: str) -> tuple[str, ...]:
    token = normalize_symbol(symbol).replace(".", "_")
    return (
        f"marketbot.v1.analysis.result.*.{token}",
        f"marketbot.v1.*.assessment.{token}",
        # These subjects use the underlying ticker; filter the selected instrument in the book.
        "marketbot.v1.leveraged-thesis.assessment.*",
        f"marketbot.v1.order-flow.state.{token}",
        f"marketbot.v1.order-flow.support.{token}",
        f"marketbot.v1.entry-setup.*.{token}",
        f"marketbot.v1.entry-watch.transition.*.{token}",
        f"marketbot.v1.entry-signal.*.{token}",
        f"marketbot.v1.alert.local.*.{token}",
        "marketbot.v1.rotation.result",
    )


class TickerWebSession:
    def __init__(
        self,
        *,
        bus: NatsJetStreamEventBus | None,
        send: Send,
        reviewer: OpenAIFailureReviewer | None,
        engines: dict[str, str],
        engine_versions: dict[str, str] | None = None,
        analyze: Analyze | None = None,
        clock: SystemClock | None = None,
        ledger_root: Path = Path(".runtime/ticker-reviews"),
        opportunities: Callable[[], tuple[EntryOpportunity, ...]] | None = None,
    ) -> None:
        self.bus = bus
        self.send = send
        self.reviewer = reviewer
        self.engines = engines
        self.engine_versions = engine_versions or {}
        self.analyze = analyze
        self.clock = clock or SystemClock()
        self.ledger_root = ledger_root
        self.opportunities = opportunities
        self.book: TickerEvidenceBook | None = None
        self._subscriptions: list[Subscription] = []
        self._pump_task: asyncio.Task[None] | None = None
        self._jobs: dict[str, asyncio.Task[None]] = {}
        self._history: list[dict[str, str]] = []
        self._transport = "UNAVAILABLE"
        self._dirty = asyncio.Event()

    async def handle(self, message: dict[str, Any]) -> None:
        kind = str(message.get("type", ""))
        request_id = str(message.get("request_id", ""))[:100]
        try:
            symbol = normalize_symbol(str(message.get("symbol", "")))
            if kind == "watch_ticker":
                await self.watch(symbol)
                return
            if kind == "stop_ticker":
                if self.book is not None and self.book.symbol != symbol:
                    raise ValueError("El ticker indicado ya no es el seleccionado.")
                await self.close()
                await self.send(
                    {"type": "ticker_stopped", "symbol": symbol, "request_id": request_id}
                )
                return
            if self.book is None or self.book.symbol != symbol:
                raise ValueError("Seleccioná primero el ticker que querés consultar.")
            if kind not in {"ask_ticker", "analyze_ticker"}:
                raise ValueError("Acción desconocida.")
            if kind in self._jobs and not self._jobs[kind].done():
                raise ValueError("Ya hay una solicitud en curso para este ticker.")
            if kind == "ask_ticker":
                if self.reviewer is None:
                    raise ValueError("Configurá MARKETBOT_OPENAI_API_KEY para consultar a GPT.")
                question = message.get("question")
                if not isinstance(question, str) or not question.strip() or len(question) > 4000:
                    raise ValueError("La pregunta debe tener entre 1 y 4000 caracteres.")
                dossier = self.snapshot()
                if not dossier["assessments"]:
                    raise ValueError("Todavía no hay assessments para enviar. Solicitá análisis.")
                self._jobs[kind] = asyncio.create_task(
                    self._ask(dossier, question, request_id),
                )
            else:
                if self.analyze is None:
                    raise ValueError("El análisis manual no está disponible.")
                self._jobs[kind] = asyncio.create_task(self._analyze(symbol, request_id))
        except ValueError as error:
            await self.send(
                {
                    "type": "error",
                    "scope": "ticker",
                    "action": kind,
                    "request_id": request_id,
                    "symbol": message.get("symbol"),
                    "message": str(error),
                }
            )

    async def watch(self, symbol: str) -> None:
        await self.close()
        self.book = TickerEvidenceBook(
            symbol,
            engines=self.engines,
            engine_versions=self.engine_versions,
        )
        self._history = []
        self._transport = "CONNECTING" if self.bus else "UNAVAILABLE"
        await self.send(self.snapshot())
        await self._refresh_transport()
        await self.send(self.snapshot())
        self._pump_task = asyncio.create_task(self._pump())

    async def _refresh_transport(self) -> None:
        if self.bus is None or self.book is None:
            return
        if not self._subscriptions:
            book = self.book

            async def receive(envelope: EventEnvelope) -> None:
                # An old callback can arrive during a switch; never mix ticker contexts.
                if self.book is not book:
                    return
                payload = envelope.model_dump(mode="json").get("payload")
                source_engine = (
                    next(
                        (engine for engine in self.engines if envelope.source.startswith(engine)),
                        None,
                    )
                    if envelope.event_type == "alert.local.produced"
                    else None
                )
                if isinstance(payload, dict) and book.merge(
                    envelope.event_type,
                    cast("dict[str, Any]", payload),
                    received_at=self.clock.now(),
                    source_engine=source_engine,
                ):
                    self._dirty.set()

            try:
                async with asyncio.timeout(20):
                    for subject in ticker_subjects(book.symbol):
                        self._subscriptions.append(
                            await self.bus.subscribe(
                                subject,
                                receive,
                                # Restore bounded current state, then consume live updates.
                                options=SubscriptionOptions(replay_latest_per_subject=True),
                            )
                        )
            except Exception:
                await self._unsubscribe()
                self._transport = "UNAVAILABLE"
                return
        try:
            async with asyncio.timeout(2):
                await asyncio.gather(
                    *(
                        self.bus.wait_until_caught_up(subscription, timeout_seconds=1)
                        for subscription in self._subscriptions
                    )
                )
            self._transport = "NATS_REPLAY_AND_LIVE"
        except TimeoutError:
            # Keep replay progressing. Unsubscribing here restarts history from zero
            # and used to leave the browser permanently without live delivery.
            self._transport = "SYNCING"
        except Exception:
            await self._unsubscribe()
            self._transport = "UNAVAILABLE"

    async def _unsubscribe(self) -> None:
        for subscription in self._subscriptions:
            with suppress(Exception):
                await subscription.unsubscribe()
        self._subscriptions.clear()

    def snapshot(self) -> dict[str, Any]:
        assert self.book is not None
        if self.opportunities is not None:
            for opportunity in self.opportunities():
                if opportunity.symbol == self.book.symbol:
                    self.book.merge(
                        "entry-opportunity.updated",
                        opportunity.model_dump(mode="json"),
                        received_at=self.clock.now(),
                    )
        snapshot: dict[str, Any] = {
            **self.book.snapshot(now=self.clock.now()),
            "transport": self._transport,
            "reconnect_enabled": self.bus is not None,
            "llm_available": self.reviewer is not None,
            "llm_model": self.reviewer.model if self.reviewer else None,
        }
        if self._transport != "NATS_REPLAY_AND_LIVE":
            for assessment in snapshot["assessments"]:
                assessment["freshness"] = "UNKNOWN"
                assessment["evaluation_freshness"] = "UNKNOWN"
                for gate in assessment["gates"]:
                    gate["status"] = "UNKNOWN"
            snapshot["short_context"] = build_short_context(
                snapshot["assessments"],
                alert_version=self.engine_versions.get("alert"),
            )
        return snapshot

    async def _pump(self) -> None:
        next_probe = self.clock.now()
        while True:
            # Coalesce bursts while keeping a heartbeat to age evidence on quiet markets.
            with suppress(TimeoutError):
                await asyncio.wait_for(self._dirty.wait(), timeout=5)
            self._dirty.clear()
            await asyncio.sleep(0.25)
            try:
                if self.bus is not None and self.clock.now() >= next_probe:
                    await self._refresh_transport()
                    next_probe = self.clock.now() + timedelta(seconds=5)
                await self.send(self.snapshot())
            except Exception:
                return

    async def _ask(self, dossier: dict[str, Any], question: str, request_id: str) -> None:
        assert self.reviewer is not None
        symbol = dossier["symbol"]
        try:
            answer = await self.reviewer.ask_ticker(
                dossier,
                question=question,
                history=tuple(self._history),
            )
            response = {
                "type": "ticker_answer",
                "symbol": symbol,
                "request_id": request_id,
                "captured_at": dossier["captured_at"],
                "revision": dossier["revision"],
                "model": self.reviewer.model,
                "question": question,
                "answer": answer,
            }
            self._history.extend(
                [{"role": "user", "content": question}, {"role": "assistant", "content": answer}]
            )
            self._history = self._history[-12:]
            try:
                await asyncio.to_thread(self._save, {**response, "snapshot": dossier})
            except OSError:
                response["save_warning"] = "La respuesta no pudo guardarse en el registro local."
            await self.send(response)
        except (FailureReviewError, ValueError) as error:
            await self.send(
                {
                    "type": "error",
                    "scope": "ticker",
                    "action": "ask_ticker",
                    "request_id": request_id,
                    "symbol": symbol,
                    "message": str(error),
                }
            )

    def _save(self, payload: dict[str, Any]) -> None:
        self.ledger_root.mkdir(parents=True, exist_ok=True)
        day = datetime.fromisoformat(payload["captured_at"]).date().isoformat()
        with (self.ledger_root / f"ticker-{day}.ndjson").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False) + "\n")

    async def _analyze(self, symbol: str, request_id: str) -> None:
        assert self.analyze is not None
        try:
            report = await self.analyze(symbol)
            await self.send(
                {
                    "type": "ticker_analysis_done",
                    "symbol": symbol,
                    "request_id": request_id,
                    "report": report,
                }
            )
        except Exception:
            await self.send(
                {
                    "type": "error",
                    "scope": "ticker",
                    "action": "analyze_ticker",
                    "request_id": request_id,
                    "symbol": symbol,
                    "message": "El análisis no pudo completarse. Consultá los logs de MarketBot.",
                }
            )

    async def close(self) -> None:
        # Invalidate callbacks before awaiting cancellation/unsubscription.
        self.book = None
        tasks = [*self._jobs.values()]
        if self._pump_task is not None:
            tasks.append(self._pump_task)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._jobs.clear()
        self._pump_task = None
        for subscription in self._subscriptions:
            with suppress(Exception):
                await subscription.unsubscribe()
        self._subscriptions.clear()
        self._history.clear()
        self._dirty.clear()
        self._transport = "STOPPED"
