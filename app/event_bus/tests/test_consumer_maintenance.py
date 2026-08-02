from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from app.event_bus.consumer_maintenance import cleanup_orphan_consumers

NOW = datetime(2026, 8, 2, 22, tzinfo=UTC)


@dataclass(frozen=True)
class _Consumer:
    name: str
    created: datetime
    push_bound: bool | None


class _Manager:
    def __init__(self, consumers: list[_Consumer]) -> None:
        self.consumers = consumers
        self.deleted: list[tuple[str, str]] = []

    async def consumers_info(self, stream: str, offset: int | None = None) -> list[_Consumer]:
        assert stream == "MARKETBOT"
        start = offset or 0
        return self.consumers[start : start + 256]

    async def delete_consumer(self, stream: str, consumer: str) -> bool:
        self.deleted.append((stream, consumer))
        return True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cleanup_deletes_only_old_disconnected_random_consumers() -> None:
    manager = _Manager(
        [
            _Consumer("mb_orphan", NOW - timedelta(hours=1), False),
            _Consumer("mb_active", NOW - timedelta(hours=1), True),
            _Consumer("mb_recent", NOW - timedelta(minutes=2), False),
            _Consumer("marketbot-long-portfolio-v1", NOW - timedelta(hours=1), False),
        ]
    )

    summary = await cleanup_orphan_consumers(
        manager,
        stream="MARKETBOT",
        now=NOW,
        minimum_age=timedelta(minutes=10),
        apply=True,
    )

    assert summary.scanned == 4
    assert summary.candidates == ("mb_orphan",)
    assert summary.deleted == ("mb_orphan",)
    assert manager.deleted == [("MARKETBOT", "mb_orphan")]
