"""Manual network export commands."""

import logging
from pathlib import Path
from typing import Annotated

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

    def ticker_cache(
        endpoint_path: Annotated[Path, typer.Option()],
        ready_path: Annotated[Path, typer.Option()],
    ) -> None:
        """Own the single in-memory cache used by independent engine processes."""
        from app.integration.ticker_cache_transport import run_cache_server

        run_cache_server(endpoint_path=endpoint_path, ready_path=ready_path)

    serve.command("ticker-cache")(ticker_cache)

    def ticker_cache_stats(endpoint_path: Annotated[Path, typer.Option()]) -> None:
        """Show cache payload sizes, deduplication and live consumer references."""
        import json

        from app.integration.ticker_cache_transport import client_from_endpoint

        endpoint = json.loads(endpoint_path.read_text(encoding="utf-8"))
        client = client_from_endpoint(endpoint)
        try:
            typer.echo(json.dumps(client.call("stats"), indent=2))
        finally:
            client.close()

    serve.command("ticker-cache-stats")(ticker_cache_stats)
