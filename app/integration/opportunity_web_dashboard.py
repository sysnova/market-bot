"""Local real-time web dashboard for MarketBot paper opportunities."""

from __future__ import annotations

import asyncio
import http
import json
import webbrowser
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import datetime, timedelta
from importlib.resources import files
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit
from uuid import UUID

from websockets.asyncio.server import ServerConnection, serve
from websockets.http11 import Request, Response

from app.common.clock import SystemClock
from app.common.settings import AppSettings, Environment
from app.contracts import (
    ENTRY_OPPORTUNITY_EVENT,
    EntryOpportunity,
    EntryOpportunityEvent,
    EventEnvelope,
    SubscriptionOptions,
)
from app.event_bus import NatsJetStreamEventBus
from app.opportunity_dashboard import (
    FailureReviewError,
    OpenAIFailureReviewer,
    build_dashboard_snapshot,
    build_failure_dossier,
)
from app.persistence import create_database_engine, create_session_factory

from .distributed_composition import write_ready
from .entry_opportunity_store import PostgresEntryOpportunityStore


class OpportunityWebBook:
    """Keep the newest bounded snapshot and the latest event reason for each opportunity."""

    def __init__(self, *, history: int) -> None:
        if history <= 0:
            raise ValueError("history must be positive")
        self.history = history
        self._items: dict[UUID, EntryOpportunity] = {}
        self._reasons: dict[UUID, tuple[str, ...]] = {}

    def merge(
        self,
        opportunity: EntryOpportunity,
        *,
        reasons: tuple[str, ...] = (),
    ) -> bool:
        current = self._items.get(opportunity.opportunity_id)
        if current is not None and (
            opportunity.revision < current.revision
            or (
                opportunity.revision == current.revision
                and opportunity.updated_at < current.updated_at
            )
        ):
            return False
        changed = current != opportunity
        self._items[opportunity.opportunity_id] = opportunity
        if reasons:
            self._reasons[opportunity.opportunity_id] = reasons
        self._trim()
        return changed or bool(reasons)

    def replace(
        self,
        opportunities: tuple[EntryOpportunity, ...],
        *,
        reasons_by_id: dict[UUID, tuple[str, ...]],
    ) -> None:
        for opportunity in opportunities:
            self.merge(
                opportunity,
                reasons=reasons_by_id.get(opportunity.opportunity_id, ()),
            )

    def items(self) -> tuple[EntryOpportunity, ...]:
        return tuple(
            sorted(
                self._items.values(),
                key=lambda item: (item.updated_at, item.symbol),
                reverse=True,
            )
        )

    def reasons(self) -> dict[str, tuple[str, ...]]:
        return {str(key): value for key, value in self._reasons.items()}

    def get(self, opportunity_id: UUID) -> EntryOpportunity | None:
        return self._items.get(opportunity_id)

    def _trim(self) -> None:
        retained = self.items()[: self.history]
        retained_ids = {item.opportunity_id for item in retained}
        self._items = {item.opportunity_id: item for item in retained}
        self._reasons = {
            key: value for key, value in self._reasons.items() if key in retained_ids
        }


async def run_opportunity_web_dashboard(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    history: int = 1000,
    refresh_interval: timedelta = timedelta(seconds=5),
    open_browser: bool = True,
    ready_path: Path | None = Path(".runtime/status/opportunity-web-dashboard.ready.json"),
) -> None:
    """Serve the local dashboard, push NATS changes, and retain PostgreSQL fallback polling."""

    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("the opportunity dashboard is intentionally localhost-only")
    if not 1 <= port <= 65535:
        raise ValueError("port must be between 1 and 65535")
    if history <= 0:
        raise ValueError("history must be positive")
    if refresh_interval <= timedelta():
        raise ValueError("refresh interval must be positive")

    settings = AppSettings()
    clock = SystemClock()
    database = create_database_engine(
        settings.database_url.get_secret_value(),
        require_ssl=settings.environment is Environment.PRODUCTION,
    )
    store = PostgresEntryOpportunityStore(create_session_factory(database))
    if not await store.is_ready():
        await database.dispose()
        raise RuntimeError(
            "entry opportunity schema is unavailable; apply "
            "20260807010000_entry_opportunity_lifecycle.sql"
        )

    reviewer = _reviewer(settings)
    book = OpportunityWebBook(history=history)
    clients: set[ServerConnection] = set()
    lock = asyncio.Lock()
    transport = "POSTGRES_POLLING"
    bus: NatsJetStreamEventBus | None = None
    subscription: Any | None = None

    await _reload_book(store=store, book=book, history=history)

    async def snapshot() -> dict[str, Any]:
        async with lock:
            payload = build_dashboard_snapshot(
                book.items(),
                refreshed_at=clock.now(),
                reasons_by_id=book.reasons(),
            )
        payload["transport"] = transport
        payload["llm_available"] = reviewer is not None
        payload["llm_model"] = reviewer.model if reviewer is not None else None
        return payload

    async def broadcast() -> None:
        if not clients:
            return
        encoded = json.dumps(await snapshot(), ensure_ascii=False)
        stale: list[ServerConnection] = []
        for client in tuple(clients):
            try:
                await client.send(encoded)
            except Exception:
                stale.append(client)
        for client in stale:
            clients.discard(client)

    async def handle_event(envelope: EventEnvelope) -> None:
        event = _opportunity_event(envelope)
        if event is None:
            return
        async with lock:
            book.merge(event.opportunity, reasons=event.reasons)
        await broadcast()

    try:
        try:
            bus = await NatsJetStreamEventBus.connect(
                servers=[settings.nats_url.get_secret_value()],
                prefix="marketbot",
                stream="MARKETBOT",
            )
            subscription = await bus.subscribe(
                "marketbot.v1.entry-opportunity.transition.>",
                handle_event,
                options=SubscriptionOptions(replay_all=False, ack_wait_seconds=60),
            )
            transport = "NATS_REALTIME_WITH_POSTGRES_FALLBACK"
        except Exception:
            if bus is not None:
                await bus.close()
                bus = None

        static_root = files("app.opportunity_dashboard").joinpath("static")

        def process_request(connection: ServerConnection, request: Request) -> Response | None:
            path = urlsplit(request.path).path
            if path == "/ws" and request.headers.get("Upgrade", "").lower() == "websocket":
                return None
            resource = {
                "/": ("index.html", "text/html; charset=utf-8"),
                "/index.html": ("index.html", "text/html; charset=utf-8"),
                "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                "/styles.css": ("styles.css", "text/css; charset=utf-8"),
            }.get(path)
            if path == "/health":
                response = connection.respond(
                    http.HTTPStatus.OK,
                    json.dumps({"status": "ok", "transport": transport}) + "\n",
                )
                response.headers["Content-Type"] = "application/json; charset=utf-8"
                return response
            if resource is None:
                return connection.respond(http.HTTPStatus.NOT_FOUND, "Not found\n")
            name, content_type = resource
            response = connection.respond(
                http.HTTPStatus.OK,
                static_root.joinpath(name).read_text(),
            )
            response.headers["Content-Type"] = content_type
            response.headers["Cache-Control"] = "no-store"
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; connect-src 'self' ws:; script-src 'self'; "
                "style-src 'self'; img-src 'self' data:"
            )
            response.headers["X-Content-Type-Options"] = "nosniff"
            return response

        async def websocket_handler(connection: ServerConnection) -> None:
            clients.add(connection)
            try:
                await connection.send(json.dumps(await snapshot(), ensure_ascii=False))
                async for raw in connection:
                    await _handle_client_message(
                        connection=connection,
                        raw=raw,
                        book=book,
                        store=store,
                        reviewer=reviewer,
                        ledger_root=Path(".runtime/thesis-reviews"),
                        snapshot=snapshot,
                    )
            finally:
                clients.discard(connection)

        web_server = await serve(
            websocket_handler,
            host,
            port,
            process_request=process_request,
            server_header="MarketBot Opportunity Dashboard",
            max_size=64 * 1024,
        )
        refresh_task = asyncio.create_task(
            _refresh_loop(
                store=store,
                book=book,
                history=history,
                refresh_interval=refresh_interval,
                lock=lock,
                broadcast=broadcast,
            )
        )
        url = f"http://{host}:{port}/"
        if ready_path is not None:
            write_ready(
                ready_path,
                {
                    "service": "opportunity-web-dashboard",
                    "url": url,
                    "history": history,
                    "refresh_interval_seconds": refresh_interval.total_seconds(),
                    "transport": transport,
                    "llm_available": reviewer is not None,
                },
            )
        if open_browser:
            await asyncio.to_thread(webbrowser.open, url)
        try:
            await web_server.serve_forever()
        finally:
            refresh_task.cancel()
            with suppress(asyncio.CancelledError):
                await refresh_task
            web_server.close()
            await web_server.wait_closed()
    finally:
        if subscription is not None:
            await subscription.unsubscribe()
        if bus is not None:
            await bus.close()
        if reviewer is not None:
            await reviewer.close()
        await database.dispose()


async def _handle_client_message(
    *,
    connection: ServerConnection,
    raw: str | bytes,
    book: OpportunityWebBook,
    store: PostgresEntryOpportunityStore,
    reviewer: OpenAIFailureReviewer | None,
    ledger_root: Path,
    snapshot: Callable[[], Awaitable[dict[str, Any]]],
) -> None:
    try:
        parsed: object = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("message must be an object")
        message = cast("dict[str, object]", parsed)
        message_type = message.get("type")
        if message_type == "refresh":
            await connection.send(json.dumps(await snapshot(), ensure_ascii=False))
            return
        if message_type != "analyze_failure":
            raise ValueError("unknown message type")
        if reviewer is None:
            raise FailureReviewError("MARKETBOT_OPENAI_API_KEY is not configured")
        opportunity_id = UUID(str(message.get("opportunity_id", "")))
        checkpoint_id = UUID(str(message.get("checkpoint_id", "")))
        notes = str(message.get("notes", ""))[:2000]
        opportunity = book.get(opportunity_id)
        if opportunity is None:
            raise ValueError("opportunity is no longer present in dashboard history")
        events = await store.list_events(opportunity_id, limit=500)
        dossier = build_failure_dossier(
            opportunity,
            checkpoint_id=checkpoint_id,
            events=events,
        )
        review = await reviewer.review(dossier, user_notes=notes)
        response = {
            "type": "failure_review",
            "opportunity_id": str(opportunity_id),
            "checkpoint_id": str(checkpoint_id),
            "symbol": opportunity.symbol,
            "reviewed_at": datetime.now().astimezone().isoformat(),
            "model": reviewer.model,
            "review": review.model_dump(mode="json"),
        }
        await _append_review(ledger_root, response)
        await connection.send(json.dumps(response, ensure_ascii=False))
    except (ValueError, TypeError, FailureReviewError) as error:
        await connection.send(
            json.dumps(
                {"type": "error", "scope": "failure_review", "message": str(error)},
                ensure_ascii=False,
            )
        )


async def _reload_book(
    *,
    store: PostgresEntryOpportunityStore,
    book: OpportunityWebBook,
    history: int,
) -> None:
    recent, active = await asyncio.gather(
        store.list_recent(limit=history),
        store.list_active(),
    )
    by_id = {item.opportunity_id: item for item in recent}
    for opportunity in active:
        current = by_id.get(opportunity.opportunity_id)
        if current is None or opportunity.revision >= current.revision:
            by_id[opportunity.opportunity_id] = opportunity
    opportunities = tuple(by_id.values())
    latest_events = await store.latest_events(tuple(by_id))
    reasons = {
        event.opportunity.opportunity_id: event.reasons for event in latest_events
    }
    book.replace(opportunities, reasons_by_id=reasons)


async def _refresh_loop(
    *,
    store: PostgresEntryOpportunityStore,
    book: OpportunityWebBook,
    history: int,
    refresh_interval: timedelta,
    lock: asyncio.Lock,
    broadcast: Callable[[], Awaitable[None]],
) -> None:
    while True:
        await asyncio.sleep(refresh_interval.total_seconds())
        async with lock:
            await _reload_book(store=store, book=book, history=history)
        await broadcast()


def _opportunity_event(envelope: EventEnvelope) -> EntryOpportunityEvent | None:
    if envelope.event_type != ENTRY_OPPORTUNITY_EVENT:
        return None
    return (
        envelope.payload
        if isinstance(envelope.payload, EntryOpportunityEvent)
        else EntryOpportunityEvent.model_validate(envelope.payload, strict=False)
    )


def _reviewer(settings: AppSettings) -> OpenAIFailureReviewer | None:
    if settings.openai_api_key is None:
        return None
    key = settings.openai_api_key.get_secret_value().strip()
    if not key:
        return None
    return OpenAIFailureReviewer(
        api_key=key,
        model=settings.thesis_review_model,
    )


async def _append_review(root: Path, payload: dict[str, Any]) -> None:
    reviewed_at = str(payload["reviewed_at"])[:10]
    path = root / f"marketbot-thesis-reviews-{reviewed_at}.ndjson"
    line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    await asyncio.to_thread(_append_text, root, path, line)


def _append_text(root: Path, path: Path, line: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(line)
