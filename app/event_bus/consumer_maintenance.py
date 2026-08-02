"""Safe maintenance for legacy random JetStream consumers."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol


class ConsumerInfo(Protocol):
    name: str
    created: datetime
    push_bound: bool | None


class ConsumerManager(Protocol):
    async def consumers_info(
        self, stream: str, offset: int | None = None
    ) -> Sequence[ConsumerInfo]: ...

    async def delete_consumer(self, stream: str, consumer: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class ConsumerCleanupSummary:
    scanned: int
    candidates: tuple[str, ...]
    deleted: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "scanned": self.scanned,
            "candidate_count": len(self.candidates),
            "deleted_count": len(self.deleted),
            "candidates": list(self.candidates),
            "deleted": list(self.deleted),
        }


async def cleanup_orphan_consumers(
    manager: ConsumerManager,
    *,
    stream: str,
    now: datetime,
    minimum_age: timedelta = timedelta(minutes=10),
    apply: bool = False,
) -> ConsumerCleanupSummary:
    """Delete only disconnected legacy consumers created with the ``mb_`` prefix."""

    consumers: list[ConsumerInfo] = []
    offset = 0
    while True:
        page = await manager.consumers_info(stream, offset=offset)
        consumers.extend(page)
        if len(page) < 256:
            break
        offset += len(page)

    cutoff = now - minimum_age
    candidates = tuple(
        sorted(
            consumer.name
            for consumer in consumers
            if consumer.name.startswith("mb_")
            and consumer.push_bound is not True
            and consumer.created <= cutoff
        )
    )
    deleted: list[str] = []
    if apply:
        for name in candidates:
            if await manager.delete_consumer(stream, name):
                deleted.append(name)
    return ConsumerCleanupSummary(
        scanned=len(consumers),
        candidates=candidates,
        deleted=tuple(deleted),
    )


async def run_orphan_consumer_cleanup(
    *,
    nats_url: str,
    stream: str = "MARKETBOT",
    minimum_age: timedelta = timedelta(minutes=10),
    apply: bool = False,
) -> ConsumerCleanupSummary:
    import nats
    from nats.js.manager import JetStreamManager

    client = await nats.connect(servers=[nats_url], connect_timeout=2)
    try:
        manager = JetStreamManager(client)
        return await cleanup_orphan_consumers(
            manager,
            stream=stream,
            now=datetime.now(UTC),
            minimum_age=minimum_age,
            apply=apply,
        )
    finally:
        await client.drain()
