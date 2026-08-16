"""JetStream contract checks against a real NATS service."""

import asyncio
import os
from uuid import uuid4

import pytest

from app.contracts import EventEnvelope
from app.event_bus import NatsJetStreamEventBus, SubscriptionOptions
from app.event_bus.stream_maintenance import purge_retained_market_bars

pytestmark = pytest.mark.integration


@pytest.mark.skipif(not os.getenv("NATS_URL"), reason="NATS_URL is not configured")
async def test_publish_subscribe_and_deduplicate(event: EventEnvelope) -> None:
    token = uuid4().hex
    bus = await NatsJetStreamEventBus.connect(
        servers=[os.environ["NATS_URL"]],
        prefix=f"marketbot_test_{token}",
        stream=f"MARKETBOT_TEST_{token.upper()}",
    )
    received: list[EventEnvelope] = []
    delivered = asyncio.Event()

    async def capture(item: EventEnvelope) -> None:
        received.append(item)
        delivered.set()

    await bus.subscribe(
        "v1.prices.updated",
        capture,
        options=SubscriptionOptions(durable_name=f"consumer_{token}", replay_all=True),
    )

    await bus.publish("v1.prices.updated", event)
    await bus.publish("v1.prices.updated", event)
    await asyncio.wait_for(delivered.wait(), timeout=3)
    await asyncio.sleep(0.2)

    assert received == [event]
    await bus.close()


@pytest.mark.skipif(not os.getenv("NATS_URL"), reason="NATS_URL is not configured")
async def test_connect_migrates_legacy_message_ttl_stream() -> None:
    import nats

    token = uuid4().hex
    prefix = f"marketbot_test_{token}"
    stream = f"MARKETBOT_TEST_{token.upper()}"
    client = await nats.connect(os.environ["NATS_URL"])
    jetstream = client.jetstream()
    bus: NatsJetStreamEventBus | None = None

    try:
        await jetstream.add_stream(
            name=stream,
            subjects=[f"{prefix}.v1.>", f"{prefix}.dlq"],
            max_age=15 * 24 * 60 * 60,
            allow_msg_ttl=True,
        )
        await jetstream.publish(
            f"{prefix}.v1.market.bar.1Min.AAPL",
            b"legacy-bar",
            headers={"Nats-TTL": "168h"},
        )
        analysis_subject = f"{prefix}.v1.analysis.result.LONG_TERM.AAPL"
        await jetstream.publish(analysis_subject, b"retained-analysis")

        bus = await NatsJetStreamEventBus.connect(
            servers=[os.environ["NATS_URL"]],
            prefix=prefix,
            stream=stream,
        )

        info = await jetstream.stream_info(stream)
        assert info.config.max_age == 7 * 24 * 60 * 60
        assert info.config.allow_msg_ttl is True

        summary = await purge_retained_market_bars(
            jetstream,  # type: ignore[arg-type]
            stream=stream,
            prefix=prefix,
            apply=True,
        )
        assert summary.messages_before == 1
        assert summary.messages_after == 0
        remaining = await jetstream.get_last_msg(stream, analysis_subject)
        assert remaining.data == b"retained-analysis"
    finally:
        if bus is not None:
            await bus.close()
        await jetstream.delete_stream(stream)
        await client.drain()
