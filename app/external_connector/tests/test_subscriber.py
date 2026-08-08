from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast

import pytest
from marketbot_connector import (
    ConnectorConfig,
    ConnectorMessage,
    EventEnvelope,
    MarketBotSubscriber,
    resolve_filters,
)
from marketbot_connector.contracts import encode_envelope
from marketbot_connector.subscriber import (
    _JetStream,
    _JetStreamManager,
    _NatsClient,
)
from nats.js.api import ConsumerConfig, RawStreamMsg, StreamConfig, StreamInfo, StreamState
from nats.js.errors import NotFoundError


@dataclass
class FakeSequence:
    stream: int = 10
    consumer: int = 2


@dataclass
class FakeMetadata:
    sequence: FakeSequence
    num_delivered: int = 1
    timestamp: datetime = datetime(2026, 8, 7, tzinfo=UTC)


class FakeMessage:
    def __init__(self, subject: str, data: bytes) -> None:
        self.subject = subject
        self.data = data
        self.metadata = FakeMetadata(sequence=FakeSequence())
        self.acks = 0
        self.naks: list[float | None] = []

    async def ack(self) -> None:
        self.acks += 1

    async def nak(self, delay: float | None = None) -> None:
        self.naks.append(delay)


class FakeSubscription:
    def __init__(self, messages: list[FakeMessage]) -> None:
        self.messages = messages
        self.fetches = 0
        self.unsubscribed = False

    async def fetch(self, batch: int, wait_seconds: float) -> list[FakeMessage]:
        assert batch > 0
        assert wait_seconds > 0
        self.fetches += 1
        if self.fetches == 1:
            return self.messages
        raise asyncio.CancelledError

    async def unsubscribe(self) -> None:
        self.unsubscribed = True


class FakeJetStream:
    def __init__(self, subscription: FakeSubscription) -> None:
        self.subscription = subscription
        self.calls: list[tuple[str, str | None, str | None, ConsumerConfig]] = []

    async def pull_subscribe(
        self,
        subject: str,
        durable: str | None = None,
        stream: str | None = None,
        config: ConsumerConfig | None = None,
        pending_msgs_limit: int = 524_288,
    ) -> FakeSubscription:
        assert config is not None
        assert pending_msgs_limit > 0
        self.calls.append((subject, durable, stream, config))
        return self.subscription


class FakeManager:
    def __init__(self, first_ts: datetime, existing: ConsumerConfig | None = None) -> None:
        self.first_ts = first_ts
        self.existing = existing

    async def stream_info(self, name: str) -> StreamInfo:
        return StreamInfo(
            config=StreamConfig(name=name),
            state=StreamState(
                messages=1,
                bytes=100,
                first_seq=1,
                last_seq=1,
                consumer_count=0,
            ),
        )

    async def get_msg(self, stream_name: str, seq: int | None = None) -> RawStreamMsg:
        assert seq == 1
        return RawStreamMsg(stream=stream_name, seq=seq, time=self.first_ts)

    async def consumer_info(self, stream: str, consumer: str) -> object:
        if self.existing is None:
            raise NotFoundError
        return SimpleNamespace(config=self.existing)

    async def delete_consumer(self, stream: str, consumer: str) -> bool:
        return True


class FakeClient:
    def __init__(self) -> None:
        self.drained = False

    async def drain(self) -> None:
        self.drained = True


def make_subscriber(
    config: ConnectorConfig,
    messages: list[FakeMessage],
    *,
    first_ts: datetime | None = None,
    existing: ConsumerConfig | None = None,
) -> tuple[MarketBotSubscriber, FakeJetStream, FakeSubscription, FakeClient]:
    subscription = FakeSubscription(messages)
    jetstream = FakeJetStream(subscription)
    client = FakeClient()
    manager = FakeManager(first_ts or datetime(2026, 8, 1, tzinfo=UTC), existing)
    subscriber = MarketBotSubscriber(
        config,
        client=cast(_NatsClient, client),
        jetstream=cast(_JetStream, jetstream),
        manager=cast(_JetStreamManager, manager),
    )
    return subscriber, jetstream, subscription, client


@pytest.mark.asyncio
async def test_prepare_uses_last_per_subject_and_bounded_pull_consumer() -> None:
    config = ConnectorConfig(filters=resolve_filters(engines=("swing",)))
    subscriber, jetstream, _, _ = make_subscriber(config, [])

    await subscriber._prepare()

    _, durable, stream, consumer = jetstream.calls[0]
    assert durable is None
    assert stream == "MARKETBOT"
    assert consumer.deliver_policy.value == "last_per_subject"
    assert consumer.filter_subjects == ["marketbot.v1.analysis.result.SWING.>"]
    assert consumer.max_ack_pending == 1_000


@pytest.mark.asyncio
async def test_start_before_retention_warns_and_uses_start_time() -> None:
    config = ConnectorConfig(
        filters=resolve_filters(engines=("swing",)),
        start_at=datetime(2026, 7, 1, tzinfo=UTC),
    )
    subscriber, jetstream, _, _ = make_subscriber(
        config, [], first_ts=datetime(2026, 8, 1, tzinfo=UTC)
    )

    await subscriber._prepare()

    assert subscriber.retention_warning is not None
    assert "continuing from" in subscriber.retention_warning
    assert jetstream.calls[0][3].deliver_policy.value == "by_start_time"


@pytest.mark.asyncio
async def test_successful_handler_acknowledges_after_delivery() -> None:
    event = EventEnvelope(event_type="analysis.result.produced", source="swing-v3")
    message = FakeMessage(
        "marketbot.v1.analysis.result.SWING.AAPL", encode_envelope(event)
    )
    config = ConnectorConfig(filters=resolve_filters(engines=("swing",)))
    subscriber, _, _, _ = make_subscriber(config, [message])
    received: list[str] = []

    async def handler(delivery: ConnectorMessage) -> None:
        assert delivery.envelope is not None
        received.append(delivery.envelope.source)

    with pytest.raises(asyncio.CancelledError):
        await subscriber.run(handler)

    assert received == ["swing-v3"]
    assert message.acks == 1
    assert message.naks == []


@pytest.mark.asyncio
async def test_handler_failure_naks_and_invalid_payload_is_preserved() -> None:
    message = FakeMessage("marketbot.dlq", b"not-json")
    config = ConnectorConfig(filters=resolve_filters(all_messages=True))
    subscriber, _, _, _ = make_subscriber(config, [message])
    errors: list[str | None] = []

    async def handler(delivery: ConnectorMessage) -> None:
        errors.append(delivery.decode_error)
        raise RuntimeError("retry")

    with pytest.raises(asyncio.CancelledError):
        await subscriber.run(handler)

    assert errors and errors[0] is not None
    assert message.acks == 0
    assert message.naks == [0.1]


@pytest.mark.asyncio
async def test_existing_durable_rejects_new_start_position() -> None:
    filters = resolve_filters(engines=("swing",))
    existing = ConsumerConfig(filter_subjects=list(filters.subjects))
    config = ConnectorConfig(
        filters=filters,
        durable_name="external_reader",
        start_at=datetime.now(UTC) - timedelta(days=1),
    )
    subscriber, _, _, _ = make_subscriber(config, [], existing=existing)

    with pytest.raises(ValueError, match="start_at only applies"):
        await subscriber._prepare()
