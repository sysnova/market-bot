"""Failure boundaries for the durable opportunity writer."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import OperationalError

from app.contracts import EventEnvelope
from app.integration.entry_opportunity_commands import (
    EntryOpportunityCommandIngress,
    EntryOpportunityCommandProcessor,
)

NOW = datetime(2026, 9, 30, 19, tzinfo=UTC)


@pytest.mark.unit
async def test_ingress_waits_through_database_outage_and_backpressure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.integration.entry_opportunity_commands as module

    unit = AsyncMock()
    unit.__aenter__.return_value = unit
    unit.entry_opportunity_commands.backlog_size.side_effect = [
        OperationalError("test", {}, Exception("database recovering")),
        10000,
        0,
    ]
    monkeypatch.setattr(module, "PersistenceUnitOfWork", lambda _: unit)
    sleep = AsyncMock()
    ingress = EntryOpportunityCommandIngress(MagicMock(), sleep=sleep)
    envelope = EventEnvelope(event_type="test.command", source="test", occurred_at=NOW, payload={})
    await ingress.enqueue(envelope, command_type="analysis", symbol="AAPL")
    assert sleep.await_count == 2
    assert (
        unit.entry_opportunity_commands.enqueue.await_args.kwargs["source_event_id"]
        == envelope.event_id
    )
    unit.entry_opportunity_commands.enqueue.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.parametrize("failure_at", ["claim_pending", "mark_processed", "commit"])
async def test_database_failure_recovers_and_does_not_leave_worker_dead(
    monkeypatch: pytest.MonkeyPatch, failure_at: str
) -> None:
    import app.integration.entry_opportunity_commands as module

    error = OperationalError("test", {}, Exception("database recovering"))
    envelope = EventEnvelope(event_type="test.command", source="test", occurred_at=NOW, payload={})
    command = MagicMock(payload=envelope.model_dump(mode="json"), command_type="analysis")
    repository = AsyncMock()
    repository.claim_pending.return_value = command
    unit = AsyncMock()
    unit.entry_opportunity_commands = repository
    unit.__aenter__.return_value = unit
    if failure_at == "commit":
        unit.__aexit__.side_effect = [error, None]
    else:
        getattr(repository, failure_at).side_effect = [
            error,
            command if failure_at == "claim_pending" else None,
        ]
    monkeypatch.setattr(module, "PersistenceUnitOfWork", lambda _: unit)
    store = MagicMock()
    apply = AsyncMock()
    health = AsyncMock()
    sleep = AsyncMock(side_effect=[None, asyncio.CancelledError()])
    processor = EntryOpportunityCommandProcessor(
        MagicMock(),
        store=store,
        apply=apply,
        clock=lambda: NOW,
        sleep=sleep,
        report_health=health,
    )

    with pytest.raises(asyncio.CancelledError):
        await processor.run()

    assert repository.claim_pending.await_count >= 2
    assert health.await_args_list[0].args[0] is False
    assert any(call.args[0] is True for call in health.await_args_list)
    assert sleep.await_args_list[0].args[0] >= 1


@pytest.mark.unit
async def test_command_effects_and_ack_share_transaction(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.integration.entry_opportunity_commands as module

    unit = AsyncMock()
    unit.__aenter__.return_value = unit
    envelope = EventEnvelope(event_type="test.command", source="test", occurred_at=NOW, payload={})
    command = MagicMock(payload=envelope.model_dump(mode="json"), command_type="analysis")
    unit.entry_opportunity_commands.claim_pending.return_value = command
    monkeypatch.setattr(module, "PersistenceUnitOfWork", lambda _: unit)
    store = MagicMock()

    async def apply(kind: str, received: EventEnvelope) -> None:
        assert kind == "analysis" and received == envelope
        store.bind.assert_called_once_with(unit)
        unit.__aexit__.assert_not_awaited()

    processor = EntryOpportunityCommandProcessor(
        MagicMock(),
        store=store,
        apply=apply,
        clock=lambda: NOW,
    )
    assert await processor.drain_once()
    unit.entry_opportunity_commands.acquire_writer.assert_awaited_once()
    unit.entry_opportunity_commands.mark_processed.assert_awaited_once()
    unit.__aexit__.assert_awaited_once_with(None, None, None)


@pytest.mark.unit
async def test_poison_command_is_preserved_and_stops_writer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.integration.entry_opportunity_commands as module

    unit = AsyncMock()
    unit.__aenter__.return_value = unit
    envelope = EventEnvelope(event_type="test.command", source="test", occurred_at=NOW, payload={})
    command = MagicMock(
        payload=envelope.model_dump(mode="json"), command_type="analysis", attempts=5
    )
    unit.entry_opportunity_commands.claim_pending.return_value = command
    monkeypatch.setattr(module, "PersistenceUnitOfWork", lambda _: unit)
    health = AsyncMock()
    processor = EntryOpportunityCommandProcessor(
        MagicMock(),
        store=MagicMock(),
        apply=AsyncMock(side_effect=ValueError("bad input")),
        clock=lambda: NOW,
        report_health=health,
    )
    with pytest.raises(RuntimeError, match="failed five times"):
        await processor.run()
    assert unit.entry_opportunity_commands.mark_failed.await_args.kwargs["terminal"] is True
    health.assert_awaited_once_with(False)
    unit.entry_opportunity_commands.mark_processed.assert_not_awaited()
