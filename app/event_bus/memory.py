"""Deterministic in-process event bus for local development and unit tests."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID

from app.contracts import EventEnvelope

from .codec import decode_envelope, encode_envelope
from .protocols import EventHandler, Subscription, SubscriptionOptions
from .subjects import subject_matches, validate_publish_subject, validate_subscription_subject


class EventBusClosedError(RuntimeError):
    """Raised when an operation targets a closed bus."""


@dataclass(slots=True)
class _Subscriber:
    subject: str
    handler: EventHandler
    options: SubscriptionOptions
    active: bool = True


class _MemorySubscription(Subscription):
    def __init__(self, subscriber: _Subscriber) -> None:
        self._subscriber = subscriber

    async def unsubscribe(self) -> None:
        self._subscriber.active = False


class InMemoryEventBus:
    """At-least-once bus with process-local history and event-id deduplication.

    Delivery is asynchronous. A handler return is the acknowledgement; an
    exception causes redelivery up to ``max_deliver``. History and deduplication
    state intentionally disappear when the process exits.
    """

    def __init__(
        self,
        *,
        prefix: str = "marketbot",
        retain_history: bool = True,
        deduplicate: bool = True,
        synchronous_delivery: bool = False,
    ) -> None:
        validate_publish_subject(prefix)
        self._prefix = prefix
        self._retain_history = retain_history
        self._deduplicate = deduplicate
        self._synchronous_delivery = synchronous_delivery
        self._subscribers: list[_Subscriber] = []
        self._history: list[tuple[str, bytes]] = []
        self._published_ids: set[UUID] = set()
        self._tasks: set[asyncio.Task[None]] = set()
        self._closed = False

    async def publish(self, subject: str, envelope: EventEnvelope) -> None:
        self._require_open()
        validate_publish_subject(subject)
        if self._deduplicate:
            if envelope.event_id in self._published_ids:
                return
            self._published_ids.add(envelope.event_id)
        payload = encode_envelope(envelope)
        if self._retain_history:
            self._history.append((subject, payload))
        await self._deliver_to_matching(subject, payload, self._subscribers)

    async def subscribe(
        self,
        subject: str,
        handler: EventHandler,
        *,
        options: SubscriptionOptions | None = None,
    ) -> Subscription:
        self._require_open()
        validate_subscription_subject(subject)
        resolved_options = options or SubscriptionOptions()
        subscriber = _Subscriber(subject, handler, resolved_options)
        self._subscribers.append(subscriber)
        if resolved_options.replay_all:
            for historical_subject, payload in self._history:
                if subject_matches(subject, historical_subject):
                    await self._start_delivery(subscriber, payload)
        return _MemorySubscription(subscriber)

    async def join(self) -> None:
        """Wait until every currently scheduled delivery has settled."""

        while self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)

    async def close(self) -> None:
        if self._closed:
            return
        for subscriber in self._subscribers:
            subscriber.active = False
        await self.join()
        self._closed = True

    async def _deliver_to_matching(
        self,
        subject: str,
        payload: bytes,
        subscribers: Iterable[_Subscriber],
    ) -> None:
        for subscriber in tuple(subscribers):
            if subscriber.active and subject_matches(subscriber.subject, subject):
                await self._start_delivery(subscriber, payload)

    async def _start_delivery(self, subscriber: _Subscriber, payload: bytes) -> None:
        if self._synchronous_delivery:
            await self._deliver(subscriber, payload)
            return
        self._schedule_delivery(subscriber, payload)

    def _schedule_delivery(self, subscriber: _Subscriber, payload: bytes) -> None:
        task = asyncio.create_task(self._deliver(subscriber, payload))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _deliver(self, subscriber: _Subscriber, payload: bytes) -> None:
        for attempt in range(subscriber.options.max_deliver):
            if not subscriber.active:
                return
            try:
                await subscriber.handler(decode_envelope(payload))
            except Exception:
                if attempt + 1 < subscriber.options.max_deliver:
                    await asyncio.sleep(subscriber.options.redelivery_delay_seconds)
            else:
                return

    def _require_open(self) -> None:
        if self._closed:
            raise EventBusClosedError("event bus is closed")
