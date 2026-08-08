"""Standalone command-line interface for the MarketBot connector."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Annotated, Any

import typer

from .catalog import ENGINE_ROUTES, resolve_filters
from .models import ConnectorConfig, ConnectorMessage, parse_start_at
from .subscriber import MarketBotSubscriber, reset_durable_consumer

app = typer.Typer(help="Consume MarketBot JetStream from a trusted external peer.")


@app.command("list-engines")
def list_engines() -> None:
    """List stable engine names and their server-side NATS filters."""

    typer.echo(
        json.dumps(
            {
                name: [route.subject for route in routes]
                for name, routes in sorted(ENGINE_ROUTES.items())
            },
            indent=2,
            sort_keys=True,
        )
    )


@app.command("subscribe")
def subscribe(
    engine: Annotated[
        list[str] | None,
        typer.Option("--engine", help="Engine name; repeat to combine engines."),
    ] = None,
    subject: Annotated[
        list[str] | None,
        typer.Option("--subject", help="NATS subject filter; repeat to combine filters."),
    ] = None,
    all_messages: Annotated[
        bool,
        typer.Option("--all", help="Consume marketbot.v1.> and marketbot.dlq."),
    ] = False,
    start_at: Annotated[
        str | None,
        typer.Option(help="RFC 3339 persisted-message start time; offset is required."),
    ] = None,
    durable: Annotated[
        str | None,
        typer.Option(help="Stable durable consumer name; omitted means ephemeral."),
    ] = None,
    url: Annotated[
        str,
        typer.Option(help="NATS URL reachable through WireGuard."),
    ] = os.getenv("MARKETBOT_CONNECTOR_URL", "nats://10.77.77.1:4222"),
    stream: Annotated[
        str,
        typer.Option(help="Existing JetStream stream name."),
    ] = os.getenv("MARKETBOT_CONNECTOR_STREAM", "MARKETBOT"),
    batch_size: Annotated[
        int,
        typer.Option(min=1, max=1_000, help="Maximum messages fetched per pull."),
    ] = 100,
    max_ack_pending: Annotated[
        int,
        typer.Option(min=1, help="Maximum unacknowledged messages."),
    ] = 1_000,
) -> None:
    """Write selected JetStream deliveries as one JSON object per line."""

    try:
        filters = resolve_filters(
            engines=tuple(engine or ()),
            subjects=tuple(subject or ()),
            all_messages=all_messages,
        )
        config = ConnectorConfig(
            filters=filters,
            url=url,
            stream=stream,
            start_at=parse_start_at(start_at) if start_at is not None else None,
            durable_name=durable,
            batch_size=batch_size,
            max_ack_pending=max_ack_pending,
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error

    async def run() -> None:
        subscriber = await MarketBotSubscriber.connect(config)
        try:
            if subscriber.retention_warning is not None:
                typer.echo(json.dumps({"warning": subscriber.retention_warning}), err=True)

            async def write_message(message: ConnectorMessage) -> None:
                sys.stdout.write(json.dumps(message.to_jsonable(), sort_keys=True) + "\n")
                sys.stdout.flush()

            await subscriber.run(write_message)
        finally:
            await subscriber.close()

    try:
        _run_async(run())
    except KeyboardInterrupt:
        typer.echo("Connector stopped.", err=True)


@app.command("reset")
def reset(
    durable: Annotated[str, typer.Argument(help="Durable consumer name to delete.")],
    yes: Annotated[
        bool,
        typer.Option("--yes", help="Confirm deletion of this consumer position."),
    ] = False,
    url: Annotated[
        str,
        typer.Option(help="NATS URL reachable through WireGuard."),
    ] = os.getenv("MARKETBOT_CONNECTOR_URL", "nats://10.77.77.1:4222"),
    stream: Annotated[
        str,
        typer.Option(help="Existing JetStream stream name."),
    ] = os.getenv("MARKETBOT_CONNECTOR_STREAM", "MARKETBOT"),
) -> None:
    """Delete one durable position without deleting any stream message."""

    if not yes:
        raise typer.BadParameter("--yes is required to delete a durable position")
    try:
        ConnectorConfig(
            filters=resolve_filters(all_messages=True),
            url=url,
            stream=stream,
            durable_name=durable,
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    deleted = _run_async(
        reset_durable_consumer(url=url, stream=stream, durable_name=durable)
    )
    typer.echo(json.dumps({"durable": durable, "deleted": deleted}, sort_keys=True))


def _run_async(awaitable: Any) -> Any:
    return asyncio.run(awaitable)


if __name__ == "__main__":
    app()
