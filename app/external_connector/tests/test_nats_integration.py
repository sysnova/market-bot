from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from marketbot_connector import (
    ConnectorConfig,
    ConnectorMessage,
    EventEnvelope,
    MarketBotSubscriber,
    resolve_filters,
)
from marketbot_connector.contracts import encode_envelope


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("NATS_URL"), reason="NATS_URL is not configured")
@pytest.mark.asyncio
async def test_real_jetstream_delivers_last_per_subject_then_live() -> None:
    import nats
    from nats.js.manager import JetStreamManager

    nats_url = os.environ["NATS_URL"]
    suffix = uuid4().hex.upper()
    stream = f"CONNECTOR_TEST_{suffix}"
    prefix = f"connector_test_{suffix.lower()}"
    admin = await nats.connect(nats_url)
    js = admin.jetstream(timeout=30)
    manager = JetStreamManager(admin, timeout=30)
    await js.add_stream(name=stream, subjects=[f"{prefix}.>"])
    try:
        await js.publish(
            f"{prefix}.swing.AAPL",
            encode_envelope(EventEnvelope(event_type="test.event", source="first")),
        )
        await js.publish(
            f"{prefix}.swing.AAPL",
            encode_envelope(EventEnvelope(event_type="test.event", source="latest")),
        )
        await js.publish(
            f"{prefix}.swing.MSFT",
            encode_envelope(EventEnvelope(event_type="test.event", source="snapshot")),
        )

        connector = await MarketBotSubscriber.connect(
            ConnectorConfig(
                filters=resolve_filters(subjects=(f"{prefix}.>",)),
                url=nats_url,
                stream=stream,
                batch_size=10,
            )
        )
        received: list[ConnectorMessage] = []
        snapshot_ready = asyncio.Event()
        target = asyncio.Event()

        async def handle(message: ConnectorMessage) -> None:
            received.append(message)
            if len(received) >= 2:
                snapshot_ready.set()
            if len(received) >= 3:
                target.set()

        task = asyncio.create_task(connector.run(handle))
        try:
            await asyncio.wait_for(snapshot_ready.wait(), timeout=10)
            await js.publish(
                f"{prefix}.swing.NVDA",
                encode_envelope(EventEnvelope(event_type="test.event", source="live")),
            )
            await asyncio.wait_for(target.wait(), timeout=10)
            await asyncio.sleep(0.1)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await connector.close()

        sources = {
            item.envelope.source for item in received if item.envelope is not None
        }
        assert sources == {"latest", "snapshot", "live"}
        assert "first" not in sources
    finally:
        await manager.delete_stream(stream)
        await admin.drain()


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("NATS_URL"), reason="NATS_URL is not configured")
@pytest.mark.asyncio
async def test_real_jetstream_starts_at_persisted_time() -> None:
    import nats
    from nats.js.manager import JetStreamManager

    nats_url = os.environ["NATS_URL"]
    suffix = uuid4().hex.upper()
    stream = f"CONNECTOR_TIME_TEST_{suffix}"
    subject = f"connector_time_test_{suffix.lower()}.events"
    admin = await nats.connect(nats_url)
    js = admin.jetstream(timeout=30)
    manager = JetStreamManager(admin, timeout=30)
    await js.add_stream(name=stream, subjects=[subject])
    try:
        await js.publish(
            subject,
            encode_envelope(EventEnvelope(event_type="test.event", source="before")),
        )
        start_at = datetime.now(UTC)
        await asyncio.sleep(0.02)
        await js.publish(
            subject,
            encode_envelope(EventEnvelope(event_type="test.event", source="after")),
        )

        connector = await MarketBotSubscriber.connect(
            ConnectorConfig(
                filters=resolve_filters(subjects=(subject,)),
                url=nats_url,
                stream=stream,
                start_at=start_at,
            )
        )
        received: list[ConnectorMessage] = []
        ready = asyncio.Event()

        async def handle(message: ConnectorMessage) -> None:
            received.append(message)
            ready.set()

        task = asyncio.create_task(connector.run(handle))
        try:
            await asyncio.wait_for(ready.wait(), timeout=10)
            await asyncio.sleep(0.1)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await connector.close()

        sources = [
            item.envelope.source for item in received if item.envelope is not None
        ]
        assert sources == ["after"]
    finally:
        await manager.delete_stream(stream)
        await admin.drain()
