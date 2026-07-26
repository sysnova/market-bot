"""JetStream contract checks against a real NATS service."""

import asyncio
import os
from uuid import uuid4

import pytest

from app.contracts import EventEnvelope
from app.event_bus import NatsJetStreamEventBus, SubscriptionOptions

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
        "prices.updated",
        capture,
        options=SubscriptionOptions(durable_name=f"consumer_{token}", replay_all=True),
    )

    await bus.publish("prices.updated", event)
    await bus.publish("prices.updated", event)
    await asyncio.wait_for(delivered.wait(), timeout=3)
    await asyncio.sleep(0.2)

    assert received == [event]
    await bus.close()
