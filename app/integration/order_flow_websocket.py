"""NATS Core and WebSocket adapters for live Order Flow export."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
from http import HTTPStatus

from nats.aio.client import Client as NatsClient
from nats.aio.msg import Msg
from pydantic import ValidationError
from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.datastructures import MultipleValuesError
from websockets.http11 import Request, Response

from app.common.settings import AppSettings
from app.contracts import (
    ORDER_FLOW_STATE_EVENT,
    ORDER_FLOW_TRANSITION_EVENT,
    OrderFlowState,
    OrderFlowTransition,
    order_flow_state_subject,
    order_flow_transition_subject,
)
from app.event_bus.codec import decode_envelope
from app.order_flow_export.gateway import OrderFlowGateway

from .engine_assembly import MarketBotAssembly

_LOG = logging.getLogger(__name__)


def input_subjects(symbols: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(subject for symbol in symbols for subject in (
        order_flow_state_subject(symbol),
        order_flow_transition_subject("placeholder", symbol).replace(".PLACEHOLDER.", ".*."),
    ))


def forward_event(gateway: OrderFlowGateway, data: bytes) -> None:
    """Validate the public payload but forward original JSON without coercing decimals."""
    try:
        envelope = decode_envelope(data)
        if envelope.event_type == ORDER_FLOW_STATE_EVENT:
            payload = OrderFlowState.model_validate_json(json.dumps(envelope.payload))
        elif envelope.event_type == ORDER_FLOW_TRANSITION_EVENT:
            payload = OrderFlowTransition.model_validate_json(json.dumps(envelope.payload))
        else:
            return
        if envelope.subject != payload.symbol or payload.symbol not in gateway.symbols:
            raise ValueError("unexpected symbol")
        gateway.publish(payload.symbol, data.decode("utf-8"))
    except (ValueError, TypeError, ValidationError):
        _LOG.warning("order_flow_ws_invalid_upstream_event")


async def start_websocket_server(
    gateway: OrderFlowGateway, *, host: str, port: int, token: str,
) -> Server:
    if not token.strip():
        raise ValueError("MARKETBOT_ORDER_FLOW_WS_TOKEN is required")
    expected = f"Bearer {token}".encode()

    def authenticate(connection: ServerConnection, request: Request) -> Response | None:
        if request.path != "/ws/order-flow":
            return connection.respond(HTTPStatus.NOT_FOUND, "Not found\n")
        try:
            supplied = request.headers.get("Authorization", "").encode("utf-8")
        except MultipleValuesError:
            supplied = b""
        if not hmac.compare_digest(supplied, expected):
            return connection.respond(HTTPStatus.UNAUTHORIZED, "Unauthorized\n")
        if not gateway.ready:
            return connection.respond(HTTPStatus.SERVICE_UNAVAILABLE, "Upstream unavailable\n")
        return None

    # The library's DEBUG logger includes handshake headers. Use an isolated logger.
    transport_log = logging.Logger("order_flow_ws.transport", level=logging.INFO)
    transport_log.addHandler(logging.NullHandler())
    transport_log.propagate = False
    return await serve(
        gateway.handle, host, port, process_request=authenticate,
        ping_interval=20, ping_timeout=20, close_timeout=5,
        max_size=16_384, max_queue=16, logger=transport_log,
    )


async def run_order_flow_websocket() -> None:
    settings = AppSettings()
    secret = settings.order_flow_ws_token
    if secret is None or not secret.get_secret_value().strip():
        raise ValueError("MARKETBOT_ORDER_FLOW_WS_TOKEN is required")
    symbols = MarketBotAssembly.from_settings(settings).build_order_flow().tracked_symbols
    gateway = OrderFlowGateway(symbols)
    core = NatsClient()
    installed = False
    server: Server | None = None

    async def disconnected() -> None:
        _LOG.warning("order_flow_ws_upstream_disconnected")
        await gateway.set_ready(False)

    async def reconnected() -> None:
        # nats-py reinstalls subscriptions before invoking this callback.
        if installed:
            await core.flush()
            await gateway.set_ready(True)
            _LOG.info("order_flow_ws_upstream_ready")

    async def nats_error(_error: Exception) -> None:
        _LOG.warning("order_flow_ws_upstream_error")

    async def handle(message: Msg) -> None:
        forward_event(gateway, message.data)

    try:
        await core.connect(
            settings.nats_url.get_secret_value(),
            disconnected_cb=disconnected, reconnected_cb=reconnected,
            error_cb=nats_error, max_reconnect_attempts=-1,
        )
        for subject in input_subjects(symbols):
            await core.subscribe(subject, cb=handle)
        await core.flush()
        installed = True
        await gateway.set_ready(core.is_connected)
        server = await start_websocket_server(
            gateway, host=settings.order_flow_ws_host, port=settings.order_flow_ws_port,
            token=secret.get_secret_value(),
        )
        _LOG.info("order_flow_ws_listening host=%s port=%s symbols=%s",
                  settings.order_flow_ws_host, settings.order_flow_ws_port, ",".join(symbols))
        await asyncio.Future[None]()
    finally:
        installed = False
        if server is not None:
            server.close(close_connections=False)
        await gateway.close()
        if server is not None:
            await server.wait_closed()
        await core.close()
