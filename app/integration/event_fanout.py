"""Fan events into the live local pipeline and best-effort durable mirrors."""

from collections.abc import Awaitable, Callable
from typing import Protocol

from app.contracts import EventEnvelope


class EventPublisher(Protocol):
    async def publish(self, subject: str, envelope: EventEnvelope) -> None: ...


MirrorErrorHandler = Callable[[str, Exception], Awaitable[None]]


class EventFanoutPublisher:
    """Publish locally first; mirror failures degrade durability, not analysis."""

    def __init__(
        self,
        *,
        primary: EventPublisher,
        mirrors: tuple[EventPublisher, ...] = (),
        on_mirror_error: MirrorErrorHandler | None = None,
    ) -> None:
        self._primary = primary
        self._mirrors = mirrors
        self._on_mirror_error = on_mirror_error

    async def publish(self, subject: str, envelope: EventEnvelope) -> None:
        await self._primary.publish(subject, envelope)
        for mirror in self._mirrors:
            try:
                await mirror.publish(subject, envelope)
            except Exception as error:
                if self._on_mirror_error is not None:
                    await self._on_mirror_error(subject, error)
