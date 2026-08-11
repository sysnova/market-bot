"""Unit-of-work transaction lifecycle tests."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.persistence.unit_of_work import PersistenceUnitOfWork


@pytest.mark.unit
@pytest.mark.asyncio
async def test_successful_context_commits_and_closes() -> None:
    session = AsyncMock()
    unit_of_work = PersistenceUnitOfWork(lambda: session)  # type: ignore[arg-type]

    async with unit_of_work as active:
        assert active.inbox is not None
        assert active.outbox is not None
        assert active.checkpoints is not None
        assert active.engine_decision_states is not None
        assert active.alert_decision_states is not None
        assert active.health is not None

    session.commit.assert_awaited_once()
    session.rollback.assert_not_awaited()
    session.close.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_failed_context_rolls_back_and_closes() -> None:
    session = AsyncMock()
    unit_of_work = PersistenceUnitOfWork(lambda: session)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="boom"):
        async with unit_of_work:
            raise RuntimeError("boom")

    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()
    session.close.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_explicit_commit_and_rollback_delegate_to_active_session() -> None:
    session = AsyncMock()
    unit_of_work = PersistenceUnitOfWork(lambda: session)  # type: ignore[arg-type]

    async with unit_of_work:
        await unit_of_work.commit()
        await unit_of_work.rollback()

    assert session.commit.await_count == 2
    session.rollback.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_operations_outside_context_are_rejected() -> None:
    unit_of_work = PersistenceUnitOfWork(AsyncMock())

    with pytest.raises(RuntimeError, match="not active"):
        await unit_of_work.commit()
