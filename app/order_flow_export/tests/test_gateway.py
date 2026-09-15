import asyncio
import json
from typing import Any

import pytest

from app.order_flow_export.gateway import OrderFlowGateway


class Socket:
    def __init__(self) -> None:
        self.incoming = asyncio.Queue()
        self.outgoing = asyncio.Queue()
        self.closed = asyncio.Event()
        self.close_code = None
        self.close_reason = None
        self.block_send = asyncio.Event()
        self.block_send.set()

    def __aiter__(self) -> Socket:
        return self

    async def __anext__(self) -> str | bytes:
        item = await self.incoming.get()
        if item is None:
            raise StopAsyncIteration
        return item

    async def send(self, message: str) -> None:
        await self.block_send.wait()
        await self.outgoing.put(json.loads(message))

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.close_code, self.close_reason = code, reason
        self.closed.set()
        self.incoming.put_nowait(None)


async def receive(socket: Socket) -> dict[str, Any]:
    return await asyncio.wait_for(socket.outgoing.get(), 1)


async def subscribe(socket: Socket, symbols: list[Any]) -> dict[str, Any]:
    socket.incoming.put_nowait(json.dumps({"action": "subscribe", "symbols": symbols}))
    return await receive(socket)


@pytest.mark.asyncio
async def test_live_delivery_subscription_replacement_and_invalid_requests() -> None:
    gateway = OrderFlowGateway(("ASTS", "NBIS"))
    await gateway.set_ready(True)
    one, two = Socket(), Socket()
    tasks = [asyncio.create_task(gateway.handle(socket)) for socket in (one, two)]
    try:
        assert await subscribe(one, [" asts ", "ASTS"]) == {
            "type": "subscribed", "symbols": ["ASTS"], "delivery": "live"
        }
        await subscribe(two, ["NBIS"])
        for invalid in ([], ["INVALID"], [1]):
            assert (await subscribe(one, invalid))["type"] == "error"
        for invalid in ('{', '[]', '{"action":"other"}', b'bytes'):
            one.incoming.put_nowait(invalid)
            assert (await receive(one))["type"] == "error"
        gateway.publish("ASTS", '{"event_type":"order-flow.state.assessed","price":"1.20"}')
        assert (await receive(one))["price"] == "1.20"
        assert two.outgoing.empty()
        await subscribe(one, ["NBIS"])
        gateway.publish("ASTS", '{"ignored":true}')
        gateway.publish("NBIS", '{"event_type":"order-flow.state.transitioned"}')
        assert (await receive(one))["event_type"] == "order-flow.state.transitioned"
        assert (await receive(two))["event_type"] == "order-flow.state.transitioned"
    finally:
        await gateway.close()
        await asyncio.gather(*tasks)


@pytest.mark.asyncio
async def test_slow_client_does_not_block_others() -> None:
    gateway = OrderFlowGateway(("ASTS",), queue_size=2)
    await gateway.set_ready(True)
    slow, fast = Socket(), Socket()
    tasks = [asyncio.create_task(gateway.handle(socket)) for socket in (slow, fast)]
    try:
        for socket in (slow, fast):
            await subscribe(socket, ["ASTS"])
        slow.block_send.clear()
        for i in range(5):
            gateway.publish("ASTS", json.dumps({"i": i}))
            assert (await receive(fast))["i"] == i
        await asyncio.wait_for(slow.closed.wait(), 1)
        assert slow.close_code == 1013
        assert slow.close_reason == "slow_consumer"
        assert not fast.closed.is_set()
    finally:
        await gateway.close()
        await asyncio.gather(*tasks)


@pytest.mark.asyncio
async def test_upstream_disconnect_and_recovery_and_shutdown() -> None:
    gateway = OrderFlowGateway(("ASTS",))
    rejected = Socket()
    await gateway.handle(rejected)
    assert rejected.close_code == 1013
    await gateway.set_ready(True)
    socket = Socket()
    task = asyncio.create_task(gateway.handle(socket))
    await subscribe(socket, ["ASTS"])
    await gateway.set_ready(False)
    await task
    assert socket.close_reason == "upstream_disconnected"
    await gateway.set_ready(True)
    fresh = Socket()
    task = asyncio.create_task(gateway.handle(fresh))
    await subscribe(fresh, ["ASTS"])
    assert fresh.outgoing.empty()  # No snapshot or replay.
    await gateway.close()
    await task
    assert fresh.close_code == 1001
