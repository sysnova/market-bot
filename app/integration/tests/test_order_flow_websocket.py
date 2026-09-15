import asyncio
import contextlib
import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from nats.aio.client import Client as NatsClient
from pydantic import SecretStr
from typer.testing import CliRunner
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from app.common.settings import AppSettings
from app.contracts import EventEnvelope, OrderFlowState, OrderFlowTransition
from app.contracts.order_flow import OrderFlowStateKind, OrderFlowWindow
from app.event_bus.codec import encode_envelope
from app.integration import order_flow_websocket as module
from app.operator_cli.main import app
from app.order_flow_export import example_client
from app.order_flow_export.gateway import OrderFlowGateway

TOKEN = "test-only-token"
NOW = datetime(2026, 9, 15, 15, tzinfo=UTC)
IDENTIFIER = UUID("019945d0-0000-7000-8000-000000000001")


def event(*, transition: bool = False, symbol: str = "ASTS") -> bytes:
    shared = dict(
        state_id=IDENTIFIER, symbol=symbol, occurred_at=NOW, engine_version="1.2.0",
        state=OrderFlowStateKind.BUY_PRESSURE, confidence=Decimal("0.80"),
        current_price=Decimal("123.4500"), reasons=("buy_pressure",),
        context_hash="sha256:" + "a" * 64,
    )
    if transition:
        payload = OrderFlowTransition(
            **shared, transition_id=IDENTIFIER, previous_state=OrderFlowStateKind.NEUTRAL,
        )
    else:
        payload = OrderFlowState(
            **shared, data_quality=Decimal("0.90"), quote_fresh=False,
            unknown_trade_ratio=Decimal("0"), windows=tuple(
                OrderFlowWindow(
                    window_seconds=seconds, trade_count=1, buy_volume=Decimal("100"),
                    sell_volume=Decimal("0"), neutral_volume=Decimal("0"),
                    unknown_volume=Decimal("0"), delta=Decimal("100"),
                    volume_velocity=Decimal("100") / seconds, large_buy_volume=Decimal("0"),
                    large_sell_volume=Decimal("0"), price_change_bps=Decimal("0"),
                ) for seconds in (1, 5, 15, 60, 300)
            ),
        )
    return encode_envelope(EventEnvelope(
        event_id=IDENTIFIER, source="order-flow", occurred_at=NOW, subject=symbol,
        event_type=("order-flow.state.transitioned" if transition else "order-flow.state.assessed"),
        payload=payload,
    ))


def test_forwarding_preserves_wire_and_rejects_invalid_events() -> None:
    class RecordingGateway(OrderFlowGateway):
        def __init__(self) -> None:
            super().__init__(("ASTS",))
            self.messages: list[tuple[str, str]] = []

        def publish(self, symbol: str, message: str) -> None:
            self.messages.append((symbol, message))

    gateway = RecordingGateway()
    for transition in (False, True):
        raw = event(transition=transition)
        module.forward_event(gateway, raw)
        assert gateway.messages[-1] == ("ASTS", raw.decode())
    for invalid in (b"{", b"null", event(symbol="NBIS")):
        module.forward_event(gateway, invalid)
    mismatched = json.loads(event())
    mismatched["subject"] = "NBIS"
    module.forward_event(gateway, json.dumps(mismatched).encode())
    malformed = json.loads(event())
    malformed["payload"]["confidence"] = "2"
    module.forward_event(gateway, json.dumps(malformed).encode())
    assert len(gateway.messages) == 2


def test_cli_and_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    assert CliRunner().invoke(app, ["serve", "order-flow", "--help"]).exit_code == 0
    monkeypatch.setenv("MARKETBOT_ORDER_FLOW_WS_TOKEN", TOKEN)
    settings = AppSettings(_env_file=None)
    assert settings.order_flow_ws_host == "127.0.0.1"
    assert settings.order_flow_ws_port == 8766
    assert TOKEN not in repr(settings)
    assert module.input_subjects(("ASTS",)) == (
        "marketbot.v1.order-flow.state.ASTS",
        "marketbot.v1.order-flow.transition.*.ASTS",
    )


def test_service_env_is_private_and_environment_can_override_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MARKETBOT_ORDER_FLOW_WS_TOKEN", raising=False)
    monkeypatch.delenv("MARKETBOT_ORDER_FLOW_WS_PORT", raising=False)
    (tmp_path / ".env").write_text("MARKETBOT_ORDER_FLOW_WS_PORT=8767\n", encoding="utf-8")
    service_env = tmp_path / "app/order_flow_export/.env"
    service_env.parent.mkdir(parents=True)
    service_env.write_text(
        "MARKETBOT_ORDER_FLOW_WS_TOKEN=service-test-secret\nMARKETBOT_ORDER_FLOW_WS_PORT=8766\n",
        encoding="utf-8",
    )
    settings = module.load_order_flow_websocket_settings(project_root=tmp_path)
    assert settings.order_flow_ws_token.get_secret_value() == "service-test-secret"
    assert settings.order_flow_ws_port == 8766
    assert "service-test-secret" not in repr(settings)
    assert AppSettings(_env_file=tmp_path / ".env").order_flow_ws_token is None
    monkeypatch.setenv("MARKETBOT_ORDER_FLOW_WS_TOKEN", "environment-test-secret")
    assert module.load_order_flow_websocket_settings(
        project_root=tmp_path,
    ).order_flow_ws_token.get_secret_value() == "environment-test-secret"


@pytest.mark.integration
async def test_loopback_auth_readiness_and_wire_delivery(caplog: pytest.LogCaptureFixture) -> None:
    gateway = OrderFlowGateway(("ASTS",))
    with pytest.raises(ValueError, match="TOKEN"):
        await module.start_websocket_server(gateway, host="127.0.0.1", port=0, token=" ")
    server = await module.start_websocket_server(
        gateway, host="127.0.0.1", port=0, token=TOKEN,
    )
    uri = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/ws/order-flow"
    caplog.set_level("DEBUG")
    try:
        for headers, suffix, status in (
            ({}, "", 401), ({"Authorization": "Bearer wrong"}, "", 401),
            ({"Authorization": f"Bearer {TOKEN}"}, "", 503),
            ({"Authorization": f"Bearer {TOKEN}"}, "/missing", 404),
            ([("Authorization", "first"), ("Authorization", "second")], "", 401),
        ):
            with pytest.raises(InvalidStatus) as caught:
                async with connect(uri + suffix, additional_headers=headers):
                    pass
            assert caught.value.response.status_code == status
        await gateway.set_ready(True)
        async with connect(uri, additional_headers={"Authorization": f"Bearer {TOKEN}"}) as ws:
            await ws.send(json.dumps({"action": "subscribe", "symbols": ["asts"]}))
            assert json.loads(await ws.recv())["symbols"] == ["ASTS"]
            raw = event()
            module.forward_event(gateway, raw)
            assert await ws.recv() == raw.decode()
            await gateway.set_ready(False)
            with pytest.raises(ConnectionClosed) as caught_close:
                await ws.recv()
            assert caught_close.value.rcvd.code == 1013
            assert caught_close.value.rcvd.reason == "upstream_disconnected"
        await gateway.set_ready(True)
        async with connect(uri, additional_headers={"Authorization": f"Bearer {TOKEN}"}) as ws:
            await gateway.close()
            with pytest.raises(ConnectionClosed) as caught_close:
                await ws.recv()
            assert caught_close.value.rcvd.code == 1001
        # Client DEBUG logs contain its own headers; server loggers must not contain credentials.
        assert all(TOKEN not in record.getMessage() for record in caplog.records
                   if not record.name.startswith("websockets.client"))
    finally:
        await gateway.close()
        server.close()
        await server.wait_closed()


@pytest.mark.integration
async def test_real_nats_to_websocket(monkeypatch: pytest.MonkeyPatch) -> None:
    url = os.environ.get("ORDER_FLOW_WS_TEST_NATS_URL")
    if not url:
        pytest.skip("Set ORDER_FLOW_WS_TEST_NATS_URL to an isolated NATS server")
    settings = AppSettings(_env_file=None, nats_url=SecretStr(url), order_flow_ws_token=TOKEN)
    monkeypatch.setattr(module, "load_order_flow_websocket_settings", lambda: settings)
    started = asyncio.Event()
    servers = []
    original = module.start_websocket_server

    async def start(gateway: OrderFlowGateway, **kwargs: Any) -> Any:
        kwargs["port"] = 0
        server = await original(gateway, **kwargs)
        servers.append(server)
        started.set()
        return server

    monkeypatch.setattr(module, "start_websocket_server", start)
    task = asyncio.create_task(module.run_order_flow_websocket())
    publisher = NatsClient()
    try:
        await asyncio.wait_for(started.wait(), 10)
        await publisher.connect(url)
        uri = f"ws://127.0.0.1:{servers[0].sockets[0].getsockname()[1]}/ws/order-flow"
        async with connect(uri, additional_headers={"Authorization": f"Bearer {TOKEN}"}) as ws:
            await ws.send('{"action":"subscribe","symbols":["ASTS"]}')
            assert json.loads(await ws.recv())["delivery"] == "live"
            for transition in (False, True):
                raw = event(transition=transition)
                subject = ("marketbot.v1.order-flow.transition.BUY_PRESSURE.ASTS" if transition
                           else "marketbot.v1.order-flow.state.ASTS")
                await publisher.publish(subject, raw)
                await publisher.flush()
                assert await asyncio.wait_for(ws.recv(), 3) == raw.decode()
    finally:
        await publisher.close()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def test_runtime_reconnect_readiness_and_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    started = asyncio.Event()
    captured = []

    class Core:
        is_connected = True

        def __init__(self) -> None:
            self.callbacks: dict[str, Any] = {}
            self.subjects: list[str] = []
            self.flushed = 0
            self.closed = False

        async def connect(self, _url: str, **kwargs: Any) -> None:
            self.callbacks = kwargs

        async def subscribe(self, subject: str, **_kwargs: Any) -> None:
            self.subjects.append(subject)

        async def flush(self) -> None:
            self.flushed += 1

        async def close(self) -> None:
            self.closed = True

    class Server:
        closed = False
        waited = False

        def close(self, **_kwargs: Any) -> None:
            self.closed = True

        async def wait_closed(self) -> None:
            self.waited = True

    core, server = Core(), Server()

    async def start(gateway: OrderFlowGateway, **_kwargs: Any) -> Server:
        captured.append(gateway)
        assert gateway.ready
        assert core.subjects and core.flushed == 1
        started.set()
        return server

    settings = AppSettings(_env_file=None, order_flow_ws_token=TOKEN)
    monkeypatch.setattr(module, "load_order_flow_websocket_settings", lambda: settings)
    monkeypatch.setattr(module, "NatsClient", lambda: core)
    monkeypatch.setattr(module, "start_websocket_server", start)
    task = asyncio.create_task(module.run_order_flow_websocket())
    try:
        await asyncio.wait_for(started.wait(), 3)
        gateway = captured[0]
        core.is_connected = False
        await core.callbacks["disconnected_cb"]()
        assert not gateway.ready
        core.is_connected = True
        await core.callbacks["reconnected_cb"]()
        assert gateway.ready
        assert core.flushed == 2
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    assert not captured[0].ready
    assert server.closed and server.waited and core.closed


async def test_missing_token_fails_before_connecting(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = AppSettings(_env_file=None, order_flow_ws_token=None)
    monkeypatch.setattr(module, "load_order_flow_websocket_settings", lambda: settings)
    with pytest.raises(ValueError, match="TOKEN"):
        await module.run_order_flow_websocket()


@pytest.mark.integration
@pytest.mark.parametrize("raw_json", [False, True])
async def test_example_client_authenticates_subscribes_and_reads_event(
    monkeypatch: pytest.MonkeyPatch, raw_json: bool,
) -> None:
    gateway = OrderFlowGateway(("ASTS",))
    await gateway.set_ready(True)
    server = await module.start_websocket_server(
        gateway, host="127.0.0.1", port=0, token=TOKEN,
    )
    subscribed = asyncio.Event()
    output: list[str] = []

    def capture(text: str, **_kwargs: Any) -> None:
        output.append(text)
        if text.startswith("Suscripto:") or '"type": "subscribed"' in text:
            subscribed.set()

    monkeypatch.setattr(example_client, "print", capture, raising=False)
    uri = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/ws/order-flow"
    task = asyncio.create_task(example_client.listen(
        uri, ["ASTS"], TOKEN, raw_json=raw_json, once=True,
    ))
    try:
        await asyncio.wait_for(subscribed.wait(), 3)
        assert not task.done()  # --once waits for data, not just the acknowledgment.
        module.forward_event(gateway, event())
        await asyncio.wait_for(task, 3)
        rendered = "\n".join(output)
        assert "ASTS" in rendered and "123.4500" in rendered
        assert TOKEN not in rendered
        if raw_json:
            assert json.loads(output[-1]) == json.loads(event())
        else:
            assert "BUY_PRESSURE" in rendered and "15s:" in rendered
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await gateway.close()
        server.close()
        await server.wait_closed()
