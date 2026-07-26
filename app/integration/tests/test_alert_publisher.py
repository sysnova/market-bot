from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.contracts import AlertSeverity, AnalysisHorizon, LocalAlert, new_uuid7
from app.integration.alert_publisher import AlertEventPublisher


class Recorder:
    def __init__(self) -> None:
        self.items: list[tuple[str, object]] = []

    async def publish(self, subject: str, envelope: object) -> None:
        self.items.append((subject, envelope))


@pytest.mark.unit
async def test_alert_publisher_wraps_local_alert_without_order_intent() -> None:
    recorder = Recorder()
    publisher = AlertEventPublisher(recorder)  # type: ignore[arg-type]
    alert = LocalAlert(
        symbol="AAPL",
        created_at=datetime(2026, 7, 24, 18, 0, tzinfo=UTC),
        severity=AlertSeverity.ACTION,
        title="AAPL BULLISH ACTION",
        message="inspect analyses",
        horizons=(AnalysisHorizon.INTRADAY,),
        component_analysis_ids=(new_uuid7(),),
        score=Decimal("80"),
        reasons=("bullish_consensus",),
        deduplication_key="aapl:action:1",
    )

    await publisher.publish(
        "alert.local.produced",
        "marketbot.v1.alert.local.ACTION.AAPL",
        alert,
    )

    subject, envelope = recorder.items[0]
    assert subject == "marketbot.v1.alert.local.ACTION.AAPL"
    assert envelope.event_type == "alert.local.produced"  # type: ignore[attr-defined]
    assert envelope.payload == alert  # type: ignore[attr-defined]
