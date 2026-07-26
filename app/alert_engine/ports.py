"""Structural boundaries for local delivery and eventual event publication."""

from __future__ import annotations

from typing import Protocol

from app.contracts import LocalAlert


class AlertSink(Protocol):
    def emit(self, alert: LocalAlert) -> object:
        """Deliver one local human notification."""


class AlertPublisher(Protocol):
    async def publish(self, event_type: str, subject: str, alert: LocalAlert) -> None:
        """Publish a shared alert contract through an external transport."""

