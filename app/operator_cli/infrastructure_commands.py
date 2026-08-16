"""External connector and NATS maintenance operator commands."""

from __future__ import annotations

import json
import os
import sys
from datetime import timedelta
from typing import Annotated

import typer

from .async_support import run_async


def register_infrastructure_commands(app: typer.Typer) -> None:
    nats_admin = typer.Typer(name="nats", help="Inspect and maintain local NATS JetStream.")
    connector = typer.Typer(
        name="connector", help="Consume MarketBot JetStream from a trusted external peer."
    )
    app.add_typer(nats_admin, name="nats")
    app.add_typer(connector, name="connector")
    connector.command("list-engines")(list_connector_engines)
    connector.command("subscribe")(subscribe_external_connector)
    connector.command("reset")(reset_external_connector)
    nats_admin.command("cleanup-consumers")(cleanup_nats_consumers)
    nats_admin.command("purge-market-bars")(purge_nats_market_bars)


def list_connector_engines() -> None:
    """List stable engine names and their server-side NATS filters."""

    from marketbot_connector import ENGINE_ROUTES

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


def subscribe_external_connector(
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

    from marketbot_connector import (
        ConnectorConfig,
        ConnectorMessage,
        MarketBotSubscriber,
        parse_start_at,
        resolve_filters,
    )

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
        run_async(run())
    except KeyboardInterrupt:
        typer.echo("Connector stopped.", err=True)


def reset_external_connector(
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
    from marketbot_connector import ConnectorConfig, reset_durable_consumer, resolve_filters

    try:
        ConnectorConfig(
            filters=resolve_filters(all_messages=True),
            url=url,
            stream=stream,
            durable_name=durable,
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    deleted = run_async(
        reset_durable_consumer(url=url, stream=stream, durable_name=durable)
    )
    typer.echo(json.dumps({"durable": durable, "deleted": deleted}, sort_keys=True))


def cleanup_nats_consumers(
    apply: Annotated[
        bool,
        typer.Option("--apply/--dry-run", help="Delete candidates; defaults to preview only."),
    ] = False,
    minimum_age_minutes: Annotated[
        int,
        typer.Option(help="Protect disconnected consumers newer than this many minutes."),
    ] = 10,
    stream: Annotated[str, typer.Option(help="JetStream stream to inspect.")] = "MARKETBOT",
) -> None:
    """Remove only disconnected legacy consumers whose generated name starts with mb_."""

    if minimum_age_minutes <= 0:
        raise typer.BadParameter("minimum age must be positive")
    from app.common.settings import AppSettings
    from app.event_bus.consumer_maintenance import run_orphan_consumer_cleanup

    settings = AppSettings()
    summary = run_async(
        run_orphan_consumer_cleanup(
            nats_url=settings.nats_url.get_secret_value(),
            stream=stream,
            minimum_age=timedelta(minutes=minimum_age_minutes),
            apply=apply,
        )
    )
    typer.echo(
        json.dumps(
            {
                "stream": stream,
                "mode": "apply" if apply else "dry-run",
                "scanned": summary.scanned,
                "candidates": len(summary.candidates),
                "deleted": len(summary.deleted),
                "candidate_preview": list(summary.candidates[:20]),
            },
            indent=2,
            sort_keys=True,
        )
    )


def purge_nats_market_bars(
    apply: Annotated[
        bool,
        typer.Option("--apply/--dry-run", help="Purge retained bars; defaults to preview only."),
    ] = False,
    stream: Annotated[str, typer.Option(help="JetStream stream to inspect.")] = "MARKETBOT",
    prefix: Annotated[str, typer.Option(help="NATS subject prefix.")] = "marketbot",
) -> None:
    """Remove only retained live bars after historical recovery moved to PostgreSQL."""

    from app.common.settings import AppSettings
    from app.event_bus.stream_maintenance import run_market_bar_purge

    settings = AppSettings()
    summary = run_async(
        run_market_bar_purge(
            nats_url=settings.nats_url.get_secret_value(),
            stream=stream,
            prefix=prefix,
            apply=apply,
        )
    )
    typer.echo(json.dumps(summary.as_dict(), indent=2, sort_keys=True))
