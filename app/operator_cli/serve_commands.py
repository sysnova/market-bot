"""Manual network export commands."""

import logging

import typer

from .async_support import run_async


def register_serve_commands(app: typer.Typer) -> None:
    serve = typer.Typer(help="Serve live data to external clients.")
    app.add_typer(serve, name="serve")

    def order_flow() -> None:
        """Export existing Order Flow events through an authenticated WebSocket."""
        from app.integration.order_flow_websocket import run_order_flow_websocket

        logging.basicConfig(level=logging.INFO)
        run_async(run_order_flow_websocket())

    serve.command("order-flow")(order_flow)
