"""NATS JetStream adapter with explicit acknowledgement and poison-message DLQ."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from time import monotonic
from typing import Protocol, cast

from pydantic import ValidationError

from app.contracts import EventEnvelope

from .codec import decode_envelope, encode_envelope
from .protocols import EventHandler, Subscription, SubscriptionOptions
from .subjects import validate_publish_subject, validate_subscription_subject

STREAM_MAX_AGE_SECONDS = 15 * 24 * 60 * 60
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


class _StreamInfo(Protocol):
    config: _StreamConfig


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

    async def stream_info(self, stream: str) -> _StreamInfo: ...

    async def add_stream(
        self, *, name: str, subjects: list[str], max_age: float
    ) -> object: ...

    async def update_stream(self, *, config: _StreamConfig) -> object: ...

    async def get_last_msg(self, stream: str, subject: str) -> _NatsMessage: ...


class _JetStreamSubscription(Subscription):
    def __init__(self, subscription: _NatsSubscription) -> None:
        self._subscription = subscription
        self._closed = False

    async def unsubscribe(self) -> None:
        if self._closed:
            return
        await self._subscription.unsubscribe()
        self._closed = True

    async def wait_until_caught_up(self, *, timeout_seconds: float = 30.0) -> None:
        deadline = monotonic() + timeout_seconds
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
    ) -> None:
        validate_publish_subject(prefix)
        self._client = client
        self._jetstream = jetstream
        self._prefix = prefix
        self._stream = stream
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
            if (
                config.subjects != desired_subjects
                or config.max_age != STREAM_MAX_AGE_SECONDS
            ):
                config.subjects = desired_subjects
                config.max_age = STREAM_MAX_AGE_SECONDS
                await typed_jetstream.update_stream(config=config)
        except NotFoundError:
            await typed_jetstream.add_stream(
                name=stream,
                subjects=desired_subjects,
                max_age=STREAM_MAX_AGE_SECONDS,
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
            deliver_policy = DeliverPolicy.LAST_PER_SUBJECT
        elif resolved.replay_all:
            deliver_policy = DeliverPolicy.ALL
        else:
            deliver_policy = DeliverPolicy.NEW

        durable_name = resolved.durable_name

        config = ConsumerConfig(
            durable_name=durable_name,
            deliver_policy=deliver_policy,
            ack_policy=AckPolicy.EXPLICIT,
            ack_wait=resolved.ack_wait_seconds,
            max_deliver=resolved.max_deliver,
        )
        async def callback(message: _NatsMessage) -> None:
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
        return subscription

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
        except (ValidationError, ValueError):
            await self._dead_letter(message)
            await message.ack()
            return
        try:
            await handler(envelope)
        except Exception:
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
