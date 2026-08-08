"""Pull-based JetStream reader for trusted external MarketBot clients."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import Protocol, cast

from nats.js.api import ConsumerConfig, ConsumerInfo, RawStreamMsg, StreamInfo
from pydantic import ValidationError

from .contracts import decode_envelope
from .models import ConnectorConfig, ConnectorMessage, validate_durable_name

ConnectorHandler = Callable[[ConnectorMessage], Awaitable[None]]

logger = logging.getLogger(__name__)


class _SequencePair(Protocol):
    stream: int
    consumer: int


class _MessageMetadata(Protocol):
    sequence: _SequencePair
    num_delivered: int
    timestamp: datetime


class _PullMessage(Protocol):
    subject: str
    data: bytes

    @property
    def metadata(self) -> _MessageMetadata: ...

    async def ack(self) -> None: ...

    async def nak(self, delay: float | None = None) -> None: ...


class _PullSubscription(Protocol):
    async def fetch(self, batch: int, wait_seconds: float) -> Sequence[_PullMessage]: ...

    async def unsubscribe(self) -> None: ...


class _JetStream(Protocol):
    async def pull_subscribe(
        self,
        subject: str,
        durable: str | None = None,
        stream: str | None = None,
        config: ConsumerConfig | None = None,
        pending_msgs_limit: int = 524_288,
    ) -> _PullSubscription: ...


class _JetStreamManager(Protocol):
    async def stream_info(self, name: str) -> StreamInfo: ...

    async def get_msg(self, stream_name: str, seq: int | None = None) -> RawStreamMsg: ...

    async def consumer_info(self, stream: str, consumer: str) -> ConsumerInfo: ...

    async def delete_consumer(self, stream: str, consumer: str) -> bool: ...


class _NatsClient(Protocol):
    async def drain(self) -> None: ...


class MarketBotSubscriber:
    """Consume retained MarketBot messages without administering the stream."""

    def __init__(
        self,
        config: ConnectorConfig,
        *,
        client: _NatsClient,
        jetstream: _JetStream,
        manager: _JetStreamManager,
    ) -> None:
        self.config = config
        self._client = client
        self._jetstream = jetstream
        self._manager = manager
        self._subscription: _PullSubscription | None = None
        self._closed = False
        self.retention_warning: str | None = None

    @classmethod
    async def connect(cls, config: ConnectorConfig) -> MarketBotSubscriber:
        """Connect to NATS without creating or updating the target stream."""

        import nats
        from nats.js.manager import JetStreamManager

        native_client = await nats.connect(
            servers=[config.url],
            connect_timeout=3,
            max_reconnect_attempts=-1,
            reconnect_time_wait=1,
            name="marketbot-external-connector",
        )
        client = cast(_NatsClient, native_client)
        instance = cls(
            config,
            client=client,
            jetstream=cast(_JetStream, native_client.jetstream(timeout=30)),
            manager=cast(_JetStreamManager, JetStreamManager(native_client, timeout=30)),
        )
        await instance._prepare()
        return instance

    async def run(self, handler: ConnectorHandler) -> None:
        """Fetch forever, acknowledging only successfully handled deliveries."""

        if self._subscription is None:
            await self._prepare()
        subscription = self._subscription
        if subscription is None:
            raise RuntimeError("connector subscription was not prepared")

        from nats.errors import TimeoutError as NatsTimeoutError

        while not self._closed:
            try:
                messages = await subscription.fetch(
                    self.config.batch_size,
                    self.config.fetch_timeout_seconds,
                )
            except (NatsTimeoutError, TimeoutError):
                continue
            for message in messages:
                delivery = self._decode(message)
                source = delivery.envelope.source if delivery.envelope is not None else None
                if not self.config.filters.accepts(message.subject, source):
                    await message.ack()
                    continue
                try:
                    await handler(delivery)
                except asyncio.CancelledError:
                    await message.nak(delay=self.config.redelivery_delay_seconds)
                    raise
                except Exception:
                    logger.exception("external connector handler failed")
                    await message.nak(delay=self.config.redelivery_delay_seconds)
                else:
                    await message.ack()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._subscription is not None:
            await self._subscription.unsubscribe()
        await self._client.drain()

    async def _prepare(self) -> None:
        if self._subscription is not None:
            return

        from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy
        from nats.js.errors import NotFoundError

        stream_info = await self._manager.stream_info(self.config.stream)
        first_ts: datetime | None = None
        if stream_info.state.messages > 0:
            try:
                first_message = await self._manager.get_msg(
                    self.config.stream, seq=stream_info.state.first_seq
                )
            except NotFoundError:
                pass
            else:
                first_ts = first_message.time
        if (
            self.config.start_at is not None
            and first_ts is not None
            and self.config.start_at < first_ts
        ):
            self.retention_warning = (
                f"requested start {self.config.start_at.isoformat()} predates retained data; "
                f"continuing from {first_ts.astimezone(UTC).isoformat()}"
            )
            logger.warning(self.retention_warning)

        deliver_policy = (
            DeliverPolicy.BY_START_TIME
            if self.config.start_at is not None
            else DeliverPolicy.LAST_PER_SUBJECT
        )
        filters = list(self.config.filters.subjects)
        consumer_config = ConsumerConfig(
            durable_name=self.config.durable_name,
            deliver_policy=deliver_policy,
            opt_start_time=self.config.start_at,
            ack_policy=AckPolicy.EXPLICIT,
            ack_wait=self.config.ack_wait_seconds,
            max_deliver=self.config.max_deliver,
            filter_subjects=filters,
            max_ack_pending=self.config.max_ack_pending,
            inactive_threshold=None if self.config.durable_name else 60.0,
        )

        if self.config.durable_name is not None:
            try:
                existing = await self._manager.consumer_info(
                    self.config.stream, self.config.durable_name
                )
            except NotFoundError:
                pass
            else:
                self._validate_existing_consumer(existing.config, filters)

        self._subscription = await self._jetstream.pull_subscribe(
            filters[0],
            durable=self.config.durable_name,
            stream=self.config.stream,
            config=consumer_config,
            pending_msgs_limit=self.config.max_ack_pending,
        )

    def _validate_existing_consumer(
        self, existing: ConsumerConfig, filters: list[str]
    ) -> None:
        configured = list(existing.filter_subjects or ())
        if not configured and existing.filter_subject:
            configured = [existing.filter_subject]
        if configured != filters:
            raise ValueError(
                "durable consumer filters differ; reset the durable before changing filters"
            )
        if self.config.start_at is not None:
            raise ValueError(
                "start_at only applies when creating a durable; reset it before changing position"
            )

    @staticmethod
    def _decode(message: _PullMessage) -> ConnectorMessage:
        metadata = message.metadata
        try:
            envelope = decode_envelope(message.data)
        except (ValidationError, ValueError, UnicodeDecodeError) as error:
            return ConnectorMessage(
                nats_subject=message.subject,
                stream_sequence=metadata.sequence.stream,
                consumer_sequence=metadata.sequence.consumer,
                delivered_count=metadata.num_delivered,
                stored_at=metadata.timestamp.astimezone(UTC),
                envelope=None,
                raw_data=message.data,
                decode_error=f"{type(error).__name__}: {error}",
            )
        return ConnectorMessage(
            nats_subject=message.subject,
            stream_sequence=metadata.sequence.stream,
            consumer_sequence=metadata.sequence.consumer,
            delivered_count=metadata.num_delivered,
            stored_at=metadata.timestamp.astimezone(UTC),
            envelope=envelope,
        )


async def reset_durable_consumer(*, url: str, stream: str, durable_name: str) -> bool:
    """Delete one explicitly named durable consumer without touching stream data."""

    validate_durable_name(durable_name)
    import nats
    from nats.js.errors import NotFoundError
    from nats.js.manager import JetStreamManager

    client = await nats.connect(servers=[url], connect_timeout=3)
    try:
        manager = JetStreamManager(client)
        try:
            return bool(await manager.delete_consumer(stream, durable_name))
        except NotFoundError:
            return False
    finally:
        await client.drain()
