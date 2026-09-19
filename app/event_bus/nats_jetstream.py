"""NATS JetStream adapter with explicit acknowledgement and poison-message DLQ."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from time import monotonic
from typing import Protocol, cast

from pydantic import ValidationError

from app.contracts import EventEnvelope

from .codec import decode_envelope, encode_envelope
from .current_state import CurrentEventStore, current_events
from .protocols import EventHandler, Subscription, SubscriptionOptions
from .subjects import validate_publish_subject, validate_subscription_subject

STREAM_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
JETSTREAM_API_TIMEOUT_SECONDS = 30.0


def stream_subjects(prefix: str) -> list[str]:
    """Return durable subjects while leaving high-volume raw ticks on Core NATS."""

    return [f"{prefix}.v1.>", f"{prefix}.dlq"]


class _NatsMessage(Protocol):
    subject: str
    data: bytes

    async def ack(self) -> None: ...

    async def nak(self, *, delay: float | None = None) -> None: ...


class _StreamConfig(Protocol):
    subjects: list[str] | None
    max_age: float
    allow_msg_ttl: bool | None


class _StreamState(Protocol):
    subjects: dict[str, int] | None


class _StreamInfo(Protocol):
    config: _StreamConfig
    state: _StreamState


class _NatsSubscription(Protocol):
    async def unsubscribe(self) -> None: ...

    async def consumer_info(self) -> _ConsumerInfo: ...


class _ConsumerInfo(Protocol):
    num_pending: int
    num_ack_pending: int


class _NatsClient(Protocol):
    @property
    def is_closed(self) -> bool: ...

    async def drain(self) -> None: ...

    async def publish(self, subject: str, payload: bytes) -> None: ...


class _JetStream(Protocol):
    async def publish(
        self,
        subject: str,
        payload: bytes = b"",
        *,
        headers: dict[str, str] | None = None,
    ) -> object: ...

    async def subscribe(
        self,
        subject: str,
        *,
        durable: str | None,
        cb: Callable[[_NatsMessage], Awaitable[None]],
        manual_ack: bool,
        config: object,
    ) -> _NatsSubscription: ...

    async def stream_info(self, stream: str, subjects_filter: str | None = None) -> _StreamInfo: ...

    async def add_stream(
        self,
        *,
        name: str,
        subjects: list[str],
        max_age: float,
        allow_msg_ttl: bool,
    ) -> object: ...

    async def update_stream(self, *, config: _StreamConfig) -> object: ...

    async def get_last_msg(self, stream: str, subject: str) -> _NatsMessage: ...


class _JetStreamSubscription(Subscription):
    def __init__(self, subscription: _NatsSubscription) -> None:
        self._subscription = subscription
        self._closed = False
        self.restore: asyncio.Task[None] | None = None

    async def unsubscribe(self) -> None:
        if self._closed:
            return
        if self.restore is not None:
            self.restore.cancel()
            await asyncio.gather(self.restore, return_exceptions=True)
        await self._subscription.unsubscribe()
        self._closed = True

    async def wait_until_caught_up(self, *, timeout_seconds: float = 30.0) -> None:
        deadline = monotonic() + timeout_seconds
        if self.restore is not None:
            await asyncio.wait_for(asyncio.shield(self.restore), timeout_seconds)
        while True:
            info = await self._subscription.consumer_info()
            if info.num_pending == 0 and info.num_ack_pending == 0:
                return
            if monotonic() >= deadline:
                raise TimeoutError("JetStream subscription did not catch up in time")
            await asyncio.sleep(0.05)


class NatsJetStreamEventBus:
    """Production adapter implementing practical at-least-once delivery."""

    def __init__(
        self,
        *,
        client: _NatsClient | None,
        jetstream: _JetStream,
        prefix: str = "marketbot",
        stream: str = "MARKETBOT",
        snapshots: CurrentEventStore | None = None,
    ) -> None:
        validate_publish_subject(prefix)
        self._client = client
        self._jetstream = jetstream
        self._prefix = prefix
        self._stream = stream
        self._snapshots = snapshots or current_events(prefix, stream)
        self._subscriptions: list[Subscription] = []
        self._closed = False

    @classmethod
    async def connect(
        cls,
        *,
        servers: Sequence[str],
        prefix: str = "marketbot",
        stream: str = "MARKETBOT",
        connect_timeout: float = 2.0,
    ) -> NatsJetStreamEventBus:
        """Connect and ensure the stream covering this bus prefix exists."""

        import nats
        from nats.js.errors import NotFoundError

        client = await nats.connect(
            servers=list(servers),
            connect_timeout=connect_timeout,
            max_reconnect_attempts=3,
            reconnect_time_wait=0.5,
        )
        jetstream = client.jetstream(timeout=JETSTREAM_API_TIMEOUT_SECONDS)
        typed_jetstream = cast(_JetStream, jetstream)
        desired_subjects = stream_subjects(prefix)
        try:
            info = await typed_jetstream.stream_info(stream)
            config = info.config
            if config.subjects != desired_subjects or config.max_age != STREAM_MAX_AGE_SECONDS:
                config.subjects = desired_subjects
                config.max_age = STREAM_MAX_AGE_SECONDS
                await typed_jetstream.update_stream(config=config)
        except NotFoundError:
            await typed_jetstream.add_stream(
                name=stream,
                subjects=desired_subjects,
                max_age=STREAM_MAX_AGE_SECONDS,
                allow_msg_ttl=False,
            )
        return cls(
            client=cast(_NatsClient, client),
            jetstream=typed_jetstream,
            prefix=prefix,
            stream=stream,
        )

    async def get_last(self, subject: str) -> EventEnvelope | None:
        """Read the last message for one exact subject without creating a consumer."""

        self._require_open()
        validate_publish_subject(subject)
        from nats.js.errors import NotFoundError

        try:
            message = await self._jetstream.get_last_msg(
                self._stream,
                self._qualify(subject),
            )
        except NotFoundError:
            return None
        return decode_envelope(message.data)

    async def publish(self, subject: str, envelope: EventEnvelope) -> None:
        self._require_open()
        validate_publish_subject(subject)
        qualified = self._qualify(subject)
        payload = encode_envelope(envelope)
        ephemeral_prefixes = (
            f"{self._prefix}.market.data.trade.",
            f"{self._prefix}.market.data.quote.",
            f"{self._prefix}.market.data.trade-correction.",
            f"{self._prefix}.market.data.trade-cancel.",
        )
        if qualified.startswith(ephemeral_prefixes):
            if self._client is None:
                raise RuntimeError("Core NATS client is unavailable")
            await self._client.publish(qualified, payload)
            return
        await self._jetstream.publish(
            qualified,
            payload,
            headers={"Nats-Msg-Id": str(envelope.event_id)},
        )
        await self._remember(qualified, envelope)

    async def subscribe(
        self,
        subject: str,
        handler: EventHandler,
        *,
        options: SubscriptionOptions | None = None,
    ) -> Subscription:
        self._require_open()
        validate_subscription_subject(subject)
        resolved = options or SubscriptionOptions()

        from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy

        if resolved.replay_latest_per_subject:
            deliver_policy = DeliverPolicy.NEW
        elif resolved.replay_all:
            deliver_policy = DeliverPolicy.ALL
        else:
            deliver_policy = DeliverPolicy.NEW

        durable_name = resolved.durable_name
        if resolved.replay_latest_per_subject and durable_name is not None:
            # Deliver policy is immutable. Leave historical consumers untouched;
            # these state subscribers resume on a separate, live-only durable.
            durable_name += "-current-v2"

        config = ConsumerConfig(
            durable_name=durable_name,
            deliver_policy=deliver_policy,
            ack_policy=AckPolicy.EXPLICIT,
            ack_wait=resolved.ack_wait_seconds,
            max_deliver=resolved.max_deliver,
            max_ack_pending=64 if resolved.replay_latest_per_subject else 1000,
        )
        restoring = asyncio.Event()
        restore_error: BaseException | None = None

        async def callback(message: _NatsMessage) -> None:
            await restoring.wait()
            if restore_error is not None:
                await message.nak(delay=resolved.redelivery_delay_seconds)
                return
            await self._deliver(message, handler, resolved)

        native = await self._jetstream.subscribe(
            self._qualify(subject),
            durable=durable_name,
            cb=callback,
            manual_ack=True,
            config=config,
        )
        subscription = _JetStreamSubscription(native)
        self._subscriptions.append(subscription)
        if resolved.replay_latest_per_subject:

            async def restore() -> None:
                nonlocal restore_error
                try:
                    await self._restore_current(self._qualify(subject), handler)
                except BaseException as error:
                    restore_error = error
                    if not isinstance(error, asyncio.CancelledError):
                        logging.getLogger(__name__).exception(
                            "Current-state restoration failed for %s", subject
                        )
                    raise
                finally:
                    restoring.set()

            subscription.restore = asyncio.create_task(restore())
            # Retrieve failures even if a caller never awaits catch-up; catch-up
            # still raises the original error and never reports incomplete state.
            subscription.restore.add_done_callback(
                lambda task: None if task.cancelled() else task.exception()
            )
        else:
            restoring.set()
        return subscription

    async def _remember(self, subject: str, envelope: EventEnvelope) -> None:
        if self._snapshots is not None and not any(
            part in subject for part in (".market.bar.", ".market.data.")
        ):
            await self._snapshots.put(subject, envelope)

    async def _restore_current(self, pattern: str, handler: EventHandler) -> None:
        if "*" in pattern or ">" in pattern:
            info = await self._jetstream.stream_info(self._stream, subjects_filter=pattern)
            subjects = info.state.subjects or {}
            # The NATS client currently exposes one page (100,000 subjects).
            # Never claim a complete restore if that page may be truncated.
            if len(subjects) >= 100_000:
                raise RuntimeError("Current-state subject listing requires pagination")
        else:
            subjects = {pattern: 1}
        for subject in subjects:
            # Reconcile one exact last event: repairs a publisher crash between
            # the JetStream acknowledgement and the Redis write, and honours purge.
            envelope = await self.get_last(subject)
            if envelope is None:
                continue
            await self._remember(subject, envelope)
            if self._snapshots is not None and envelope.event_type == "analysis.result.produced":
                envelope = await self._snapshots.get(subject) or envelope
            await handler(envelope)

    async def wait_until_caught_up(
        self, subscription: Subscription, *, timeout_seconds: float = 30.0
    ) -> None:
        if not isinstance(subscription, _JetStreamSubscription):
            raise TypeError("subscription was not created by this JetStream bus")
        await subscription.wait_until_caught_up(timeout_seconds=timeout_seconds)

    async def close(self) -> None:
        if self._closed:
            return
        for subscription in self._subscriptions:
            await subscription.unsubscribe()
        if self._client is not None and not self._client.is_closed:
            await self._client.drain()
        self._closed = True

    async def _deliver(
        self,
        message: _NatsMessage,
        handler: EventHandler,
        options: SubscriptionOptions | None = None,
    ) -> None:
        resolved = options or SubscriptionOptions()
        try:
            envelope = decode_envelope(message.data)
        except ValidationError, ValueError:
            await self._dead_letter(message)
            await message.ack()
            return
        try:
            await self._remember(message.subject, envelope)
            await handler(envelope)
        except Exception:
            logging.getLogger(__name__).exception(
                "JetStream handler failed for subject %s event %s; NAKing for redelivery",
                message.subject,
                envelope.event_id,
            )
            await message.nak(delay=resolved.redelivery_delay_seconds)
            return
        await message.ack()

    async def _dead_letter(self, message: _NatsMessage) -> None:
        if message.subject == self._qualify("dlq"):
            return
        await self._jetstream.publish(
            self._qualify("dlq"),
            message.data,
            headers={
                "X-Original-Subject": message.subject,
                "X-Dead-Letter-Reason": "invalid-event-envelope",
            },
        )

    def _qualify(self, subject: str) -> str:
        if subject == self._prefix or subject.startswith(f"{self._prefix}."):
            return subject
        return f"{self._prefix}.{subject}"

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("event bus is closed")
