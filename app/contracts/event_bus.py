"""Stable event transport ports shared by engines and bus adapters."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .rules import EventEnvelope

EventHandler = Callable[[EventEnvelope], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class SubscriptionOptions:
    """Transport-neutral at-least-once delivery controls."""

    durable_name: str | None = None
    replay_all: bool = False
    max_deliver: int = 5
    ack_wait_seconds: float = 30.0
    redelivery_delay_seconds: float = 0.1

    def __post_init__(self) -> None:
        if self.durable_name is not None and not self.durable_name.strip():
            raise ValueError("durable_name cannot be blank")
        if self.max_deliver < 1:
            raise ValueError("max_deliver must be at least one")
        if self.ack_wait_seconds <= 0:
            raise ValueError("ack_wait_seconds must be positive")
        if self.redelivery_delay_seconds < 0:
            raise ValueError("redelivery_delay_seconds cannot be negative")


@runtime_checkable
class Subscription(Protocol):
    """Handle for stopping one subscription."""

    async def unsubscribe(self) -> None:
        """Stop future deliveries to this subscriber."""


@runtime_checkable
class EventBus(Protocol):
    """At-least-once event transport boundary."""

    async def publish(self, subject: str, envelope: EventEnvelope) -> None:
        """Publish an envelope, using its event id as the deduplication identity."""

    async def subscribe(
        self,
        subject: str,
        handler: EventHandler,
        *,
        options: SubscriptionOptions | None = None,
    ) -> Subscription:
        """Subscribe with acknowledgement after successful handling."""

        raise NotImplementedError

    async def close(self) -> None:
        """Release transport resources."""
