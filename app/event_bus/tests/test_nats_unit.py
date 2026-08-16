"""Unit tests for JetStream message handling without a NATS server."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from app.contracts import EventEnvelope
from app.event_bus import SubscriptionOptions
from app.event_bus.codec import encode_envelope
from app.event_bus.nats_jetstream import (
    JETSTREAM_API_TIMEOUT_SECONDS,
    STREAM_MAX_AGE_SECONDS,
    NatsJetStreamEventBus,
    stream_subjects,
)


@dataclass
class FakeMessage:
    subject: str
    data: bytes
    headers: dict[str, str] | None = None
    acked: int = 0
    naked: int = 0

    async def ack(self) -> None:
        self.acked += 1

    async def nak(self, **_: Any) -> None:
        self.naked += 1


@dataclass
class FakeJetStream:
    published: list[tuple[str, bytes, dict[str, str] | None]] = field(default_factory=list)
    subscription: FakeSubscription = field(default_factory=lambda: FakeSubscription())
    subscribed: list[tuple[str, str | None, object]] = field(default_factory=list)
    stream_queries: list[str] = field(default_factory=list)
    streams_added: list[tuple[str, list[str], float, bool]] = field(default_factory=list)
    streams_updated: list[object] = field(default_factory=list)
    last_messages: dict[str, FakeMessage] = field(default_factory=dict)
    stream_info_error: Exception | None = None
    allow_msg_ttl: bool = True
    max_age_seconds: float = 15 * 24 * 60 * 60

    async def publish(
        self, subject: str, payload: bytes, *, headers: dict[str, str] | None = None
    ) -> None:
        self.published.append((subject, payload, headers))

    async def subscribe(
        self,
        subject: str,
        *,
        durable: str | None,
        cb: Callable[[FakeMessage], Awaitable[None]],
        manual_ack: bool,
        config: object,
    ) -> FakeSubscription:
        assert manual_ack
        assert cb
        self.subscribed.append((subject, durable, config))
        return self.subscription

    async def stream_info(self, stream: str) -> object:
        self.stream_queries.append(stream)
        if self.stream_info_error is not None:
            raise self.stream_info_error
        return SimpleNamespace(
            config=SimpleNamespace(
                subjects=stream_subjects("marketbot"),
                max_age=self.max_age_seconds,
                allow_msg_ttl=self.allow_msg_ttl,
            )
        )

    async def add_stream(
        self, *, name: str, subjects: list[str], max_age: float, allow_msg_ttl: bool
    ) -> object:
        self.streams_added.append((name, subjects, max_age, allow_msg_ttl))
        return object()

    async def update_stream(self, *, config: object) -> object:
        self.streams_updated.append(config)
        return object()

    async def get_last_msg(self, stream: str, subject: str) -> FakeMessage:
        assert stream == "MARKETBOT"
        return self.last_messages[subject]


@dataclass
class FakeSubscription:
    unsubscribed: int = 0

    async def unsubscribe(self) -> None:
        self.unsubscribed += 1

    async def consumer_info(self) -> object:
        return SimpleNamespace(num_pending=0, num_ack_pending=0)


@dataclass
class FakeClient:
    js: FakeJetStream
    is_closed: bool = False
    drains: int = 0
    jetstream_timeouts: list[float] = field(default_factory=list)

    def jetstream(self, *, timeout: float) -> FakeJetStream:
        self.jetstream_timeouts.append(timeout)
        return self.js

    async def drain(self) -> None:
        self.drains += 1
        self.is_closed = True


@pytest.mark.unit
def test_stream_subjects_persist_only_versioned_events_and_dlq() -> None:
    assert stream_subjects("marketbot") == ["marketbot.v1.>", "marketbot.dlq"]


@pytest.mark.unit
async def test_connect_uses_extended_jetstream_api_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    import nats

    js = FakeJetStream()
    client = FakeClient(js)

    async def connect(**_: Any) -> FakeClient:
        return client

    monkeypatch.setattr(nats, "connect", connect)

    bus = await NatsJetStreamEventBus.connect(servers=["nats://localhost:4222"])

    assert client.jetstream_timeouts == [JETSTREAM_API_TIMEOUT_SECONDS]
    await bus.close()


@pytest.mark.unit
async def test_connect_does_not_create_stream_after_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import nats
    from nats.errors import TimeoutError as NatsTimeoutError

    js = FakeJetStream(stream_info_error=NatsTimeoutError())
    client = FakeClient(js)

    async def connect(**_: Any) -> FakeClient:
        return client

    monkeypatch.setattr(nats, "connect", connect)

    with pytest.raises(NatsTimeoutError):
        await NatsJetStreamEventBus.connect(servers=["nats://localhost:4222"])

    assert js.streams_added == []


@pytest.mark.unit
async def test_connect_creates_stream_only_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import nats
    from nats.js.errors import NotFoundError

    js = FakeJetStream(stream_info_error=NotFoundError())
    client = FakeClient(js)

    async def connect(**_: Any) -> FakeClient:
        return client

    monkeypatch.setattr(nats, "connect", connect)

    bus = await NatsJetStreamEventBus.connect(servers=["nats://localhost:4222"])

    assert js.streams_added == [
        (
            "MARKETBOT",
            ["marketbot.v1.>", "marketbot.dlq"],
            7 * 24 * 60 * 60,
            False,
        )
    ]
    await bus.close()


@pytest.mark.unit
async def test_connect_migrates_existing_stream_to_global_seven_day_retention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import nats

    js = FakeJetStream(
        allow_msg_ttl=True,
        max_age_seconds=15 * 24 * 60 * 60,
    )
    client = FakeClient(js)

    async def connect(**_: Any) -> FakeClient:
        return client

    monkeypatch.setattr(nats, "connect", connect)

    bus = await NatsJetStreamEventBus.connect(servers=["nats://localhost:4222"])

    assert len(js.streams_updated) == 1
    assert js.streams_updated[0].max_age == 7 * 24 * 60 * 60
    assert js.streams_updated[0].allow_msg_ttl is True
    await bus.close()


@pytest.mark.unit
async def test_connect_keeps_compliant_stream_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import nats

    js = FakeJetStream(
        allow_msg_ttl=True,
        max_age_seconds=STREAM_MAX_AGE_SECONDS,
    )
    client = FakeClient(js)

    async def connect(**_: Any) -> FakeClient:
        return client

    monkeypatch.setattr(nats, "connect", connect)

    bus = await NatsJetStreamEventBus.connect(servers=["nats://localhost:4222"])

    assert js.streams_updated == []
    await bus.close()


@pytest.mark.unit
async def test_publish_sets_jetstream_deduplication_header(event: EventEnvelope) -> None:
    js = FakeJetStream()
    bus = NatsJetStreamEventBus(client=None, jetstream=js, prefix="marketbot")  # type: ignore[arg-type]

    await bus.publish("prices.updated", event)

    assert js.published == [
        (
            "marketbot.prices.updated",
            encode_envelope(event),
            {"Nats-Msg-Id": str(event.event_id)},
        )
    ]


@pytest.mark.unit
async def test_publish_does_not_duplicate_an_already_qualified_prefix(
    event: EventEnvelope,
) -> None:
    js = FakeJetStream()
    bus = NatsJetStreamEventBus(client=None, jetstream=js, prefix="marketbot")  # type: ignore[arg-type]

    await bus.publish("marketbot.v1.market.bar.1Min.AAPL", event)

    assert js.published[0][0] == "marketbot.v1.market.bar.1Min.AAPL"


@pytest.mark.unit
async def test_publish_market_bar_uses_live_subject_without_per_message_ttl(
    event: EventEnvelope,
) -> None:
    js = FakeJetStream()
    bus = NatsJetStreamEventBus(client=None, jetstream=js, prefix="marketbot")  # type: ignore[arg-type]

    await bus.publish("marketbot.v1.market.bar.1Min.AAPL", event)

    assert js.published == [
        (
            "marketbot.v1.market.bar.1Min.AAPL",
            encode_envelope(event),
            {"Nats-Msg-Id": str(event.event_id)},
        )
    ]


@pytest.mark.unit
async def test_get_last_reads_one_exact_subject_without_creating_consumer(
    event: EventEnvelope,
) -> None:
    js = FakeJetStream()
    js.last_messages["marketbot.v1.analysis.result.LONG_TERM.TGT"] = FakeMessage(
        "marketbot.v1.analysis.result.LONG_TERM.TGT",
        encode_envelope(event),
    )
    bus = NatsJetStreamEventBus(client=None, jetstream=js, prefix="marketbot")  # type: ignore[arg-type]

    restored = await bus.get_last("marketbot.v1.analysis.result.LONG_TERM.TGT")

    assert restored == event
    assert js.subscribed == []


@pytest.mark.unit
async def test_subscribe_configures_durable_explicit_ack_and_replay() -> None:
    from nats.js.api import AckPolicy, DeliverPolicy

    js = FakeJetStream()
    bus = NatsJetStreamEventBus(client=None, jetstream=js, prefix="marketbot")  # type: ignore[arg-type]

    subscription = await bus.subscribe(
        "prices.*",
        _discard,
        options=SubscriptionOptions(
            durable_name="price_reader",
            replay_all=True,
            max_deliver=7,
            ack_wait_seconds=12,
        ),
    )

    assert js.subscribed[0][0:2] == ("marketbot.prices.*", "price_reader")
    config = js.subscribed[0][2]
    assert config.ack_policy is AckPolicy.EXPLICIT  # type: ignore[attr-defined]
    assert config.deliver_policy is DeliverPolicy.ALL  # type: ignore[attr-defined]
    assert config.max_deliver == 7  # type: ignore[attr-defined]
    await subscription.unsubscribe()
    assert js.subscription.unsubscribed == 1


@pytest.mark.unit
async def test_subscribe_can_hydrate_only_latest_message_per_subject() -> None:
    from nats.js.api import DeliverPolicy

    js = FakeJetStream()
    bus = NatsJetStreamEventBus(client=None, jetstream=js, prefix="marketbot")  # type: ignore[arg-type]

    subscription = await bus.subscribe(
        "v1.analysis.result.>",
        _discard,
        options=SubscriptionOptions(replay_latest_per_subject=True),
    )

    _, durable, config = js.subscribed[0]
    assert durable is None
    assert config.durable_name is None  # type: ignore[attr-defined]
    assert config.deliver_policy is DeliverPolicy.LAST_PER_SUBJECT  # type: ignore[attr-defined]
    await bus.wait_until_caught_up(subscription)


@pytest.mark.unit
async def test_subscribe_without_explicit_durable_is_ephemeral() -> None:
    from nats.js.api import DeliverPolicy

    js = FakeJetStream()
    bus = NatsJetStreamEventBus(client=None, jetstream=js, prefix="marketbot")  # type: ignore[arg-type]

    await bus.subscribe("v1.alert.local.>", _discard)

    _, durable, config = js.subscribed[0]
    assert durable is None
    assert config.durable_name is None  # type: ignore[attr-defined]
    assert config.deliver_policy is DeliverPolicy.NEW  # type: ignore[attr-defined]


@pytest.mark.unit
async def test_close_unsubscribes_and_drains_once() -> None:
    js = FakeJetStream()
    client = FakeClient(js)
    bus = NatsJetStreamEventBus(client=client, jetstream=js, prefix="marketbot")  # type: ignore[arg-type]
    await bus.subscribe("prices.updated", _discard)

    await bus.close()
    await bus.close()

    assert js.subscription.unsubscribed == 1
    assert client.drains == 1


@pytest.mark.unit
async def test_close_does_not_unsubscribe_an_explicitly_closed_subscription_twice() -> None:
    js = FakeJetStream()
    client = FakeClient(js)
    bus = NatsJetStreamEventBus(client=client, jetstream=js, prefix="marketbot")  # type: ignore[arg-type]
    subscription = await bus.subscribe("prices.updated", _discard)

    await subscription.unsubscribe()
    await bus.close()

    assert js.subscription.unsubscribed == 1
    assert client.drains == 1


@pytest.mark.unit
async def test_valid_message_acks_only_after_handler_success(event: EventEnvelope) -> None:
    js = FakeJetStream()
    bus = NatsJetStreamEventBus(client=None, jetstream=js, prefix="marketbot")  # type: ignore[arg-type]
    message = FakeMessage("marketbot.prices.updated", encode_envelope(event))
    received: list[EventEnvelope] = []

    await bus._deliver(message, lambda item: _append(received, item))

    assert received == [event]
    assert message.acked == 1
    assert message.naked == 0


@pytest.mark.unit
async def test_handler_failure_naks_for_redelivery(event: EventEnvelope) -> None:
    js = FakeJetStream()
    bus = NatsJetStreamEventBus(client=None, jetstream=js, prefix="marketbot")  # type: ignore[arg-type]
    message = FakeMessage("marketbot.prices.updated", encode_envelope(event))

    async def fail(_: EventEnvelope) -> None:
        raise RuntimeError("transient")

    await bus._deliver(message, fail)

    assert message.acked == 0
    assert message.naked == 1


@pytest.mark.unit
async def test_invalid_envelope_goes_to_dlq_then_is_acked() -> None:
    js = FakeJetStream()
    bus = NatsJetStreamEventBus(client=None, jetstream=js, prefix="marketbot")  # type: ignore[arg-type]
    message = FakeMessage("marketbot.prices.updated", b"not-json")

    await bus._deliver(message, _discard)

    assert message.acked == 1
    assert message.naked == 0
    assert js.published[0][0] == "marketbot.dlq"
    assert js.published[0][1] == b"not-json"
    assert js.published[0][2]["X-Original-Subject"] == "marketbot.prices.updated"


@pytest.mark.unit
async def test_invalid_message_already_on_dlq_is_only_acked() -> None:
    js = FakeJetStream()
    bus = NatsJetStreamEventBus(client=None, jetstream=js, prefix="marketbot")  # type: ignore[arg-type]
    message = FakeMessage("marketbot.dlq", b"still-not-json")

    await bus._deliver(message, _discard)

    assert message.acked == 1
    assert js.published == []


async def _append(items: list[EventEnvelope], item: EventEnvelope) -> None:
    items.append(item)


async def _discard(_: EventEnvelope) -> None:
    return None
