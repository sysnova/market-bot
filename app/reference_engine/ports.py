"""Structural ports used by the reference engine."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol

from app.contracts import EventEnvelope

from .models import EngineEvaluation

EventHandler = Callable[[EventEnvelope], Awaitable[None]]


class SubscriptionPort(Protocol):
    async def unsubscribe(self) -> None: ...


class EventBusPort(Protocol):
    async def subscribe(self, subject: str, handler: EventHandler) -> SubscriptionPort: ...


class EvaluationSink(Protocol):
    """Idempotent sink keyed by ``EngineEvaluation.decision_id``."""

    async def emit(self, evaluation: EngineEvaluation) -> None: ...
