from datetime import UTC, datetime, timedelta

import fakeredis

from app.contracts import EventEnvelope
from app.event_bus import SubscriptionOptions
from app.event_bus.codec import encode_envelope
from app.event_bus.nats_jetstream import NatsJetStreamEventBus
from app.event_bus.tests.test_nats_unit import FakeJetStream, FakeMessage
from app.integration.redis_current_events import RedisCurrentEvents


async def test_current_analysis_survives_restart_and_older_bootstrap() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    store = RedisCurrentEvents(redis, namespace="test:")
    now = datetime(2026, 9, 16, tzinfo=UTC)
    current = EventEnvelope(
        event_type="analysis.result.produced",
        source="swing",
        occurred_at=now,
        payload={"as_of": now.isoformat()},
    )
    old = current.model_copy(
        update={
            "occurred_at": now + timedelta(minutes=1),
            "payload": {"as_of": (now - timedelta(hours=1)).isoformat()},
        }
    )
    await store.put("analysis.SWING.AAPL", current)
    await store.put("analysis.SWING.AAPL", old)
    restarted = RedisCurrentEvents(redis, namespace="test:")
    assert await restarted.get("analysis.SWING.AAPL") == current
    assert await restarted.get("analysis.SWING.MSFT") is None
    assert redis.ttl("test:analysis.SWING.AAPL") > 0
    assert redis.dbsize() == 1


async def test_new_evaluation_and_equal_time_correction_replace_snapshot() -> None:
    store = RedisCurrentEvents(fakeredis.FakeRedis(), namespace="test:")
    old = EventEnvelope(
        event_type="analysis.result.produced",
        source="swing",
        payload={"as_of": "2026-09-16T12:00:00Z", "assessed_at": "2026-09-16T12:05:00Z"},
    )
    newer = old.model_copy(
        update={
            "payload": {
                "as_of": "2026-09-16T12:00:00Z",
                "assessed_at": "2026-09-16T12:06:00Z",
                "score": 80,
            }
        }
    )
    correction = newer.model_copy(update={"payload": {**newer.payload, "score": 81}})
    for event in (old, newer, old, correction):
        await store.put("analysis.SWING.AAPL", event)
    assert await store.get("analysis.SWING.AAPL") == correction


async def test_restart_reconciles_nats_without_replacing_newer_cached_analysis() -> None:
    subject = "marketbot.v1.analysis.result.SWING.AAPL"
    store = RedisCurrentEvents(fakeredis.FakeRedis(), namespace="test:")
    recent = EventEnvelope(
        event_type="analysis.result.produced",
        source="swing",
        payload={"as_of": "2026-09-16T12:00:00Z"},
    )
    old = recent.model_copy(update={"payload": {"as_of": "2026-09-15T12:00:00Z"}})
    await store.put(subject, recent)
    # Simulate a restart publishing an old bootstrap result last in JetStream.
    js = FakeJetStream(last_messages={subject: FakeMessage(subject, encode_envelope(old))})
    bus = NatsJetStreamEventBus(client=None, jetstream=js, snapshots=store)  # type: ignore[arg-type]
    received: list[EventEnvelope] = []

    async def receive(envelope: EventEnvelope) -> None:
        received.append(envelope)

    try:
        sub = await bus.subscribe(
            subject, receive, options=SubscriptionOptions(replay_latest_per_subject=True)
        )
        await bus.wait_until_caught_up(sub)
        assert received == [recent]
        # A crash after the NATS ack and before Redis must also be repaired.
        newer = recent.model_copy(update={"payload": {"as_of": "2026-09-16T12:15:00Z"}})
        js.last_messages[subject] = FakeMessage(subject, encode_envelope(newer))
        sub = await bus.subscribe(
            subject, receive, options=SubscriptionOptions(replay_latest_per_subject=True)
        )
        await bus.wait_until_caught_up(sub)
        assert received[-1] == newer
        assert await store.get(subject) == newer
    finally:
        await bus.close()
