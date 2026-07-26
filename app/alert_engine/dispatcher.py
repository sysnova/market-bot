"""Delivery orchestration kept separate from scoring policy."""

from __future__ import annotations

from app.contracts import LOCAL_ALERT_EVENT, LocalAlert, local_alert_subject

from .ports import AlertPublisher, AlertSink


class AlertDispatcher:
    def __init__(
        self,
        *,
        sinks: tuple[AlertSink, ...],
        publisher: AlertPublisher | None = None,
    ) -> None:
        self._sinks = sinks
        self._publisher = publisher

    async def dispatch(self, alert: LocalAlert) -> None:
        for sink in self._sinks:
            sink.emit(alert)
        if self._publisher is not None:
            await self._publisher.publish(
                LOCAL_ALERT_EVENT,
                local_alert_subject(alert.severity, alert.symbol),
                alert,
            )

