import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.contracts import (
    SERVICE_HEALTH_EVENT,
    AnalysisHorizon,
    BarTimeframe,
    EventEnvelope,
    ServiceHealth,
)
from app.integration.distributed_composition import (
    _batches,
    _build_worker,
    _publish_health,
    _service_name,
    _weekly_bar_is_complete,
    _write_ready,
    engine_history_requests,
    engine_live_subjects,
)
from app.integration.intraday_worker import IntradayWorker
from app.integration.long_term_worker import LongTermWorker
from app.integration.swing_worker import SwingWorker


class RecordingPublisher:
    def __init__(self) -> None:
        self.events: list[tuple[str, EventEnvelope]] = []

    async def publish(self, subject: str, envelope: EventEnvelope) -> None:
        self.events.append((subject, envelope))


@pytest.mark.unit
def test_each_engine_requests_only_its_own_historical_context() -> None:
    long = engine_history_requests(AnalysisHorizon.LONG_TERM)
    swing = engine_history_requests(AnalysisHorizon.SWING)
    intraday = engine_history_requests(AnalysisHorizon.INTRADAY)

    assert [(item.timeframe, item.max_bars_per_symbol) for item in long] == [
        (BarTimeframe.DAY_1, 260),
        (BarTimeframe.WEEK_1, 220),
    ]
    assert [(item.timeframe, item.max_bars_per_symbol) for item in swing] == [
        (BarTimeframe.DAY_1, 120),
        (BarTimeframe.MINUTE_15, 160),
    ]
    assert [(item.timeframe, item.max_bars_per_symbol) for item in intraday] == [
        (BarTimeframe.MINUTE_1, 500),
    ]
    assert long[1].lookback == timedelta(days=365 * 5)


@pytest.mark.unit
def test_each_engine_has_an_independent_live_subscription_set() -> None:
    assert engine_live_subjects(AnalysisHorizon.LONG_TERM) == (
        "marketbot.v1.market.bar.1Min.>",
        "marketbot.v1.market.bar.1Day.>",
        "marketbot.v1.market.bar.1Week.>",
    )
    assert engine_live_subjects(AnalysisHorizon.SWING) == (
        "marketbot.v1.market.bar.1Min.>",
        "marketbot.v1.market.bar.1Day.>",
    )
    assert engine_live_subjects(AnalysisHorizon.INTRADAY) == (
        "marketbot.v1.market.bar.1Min.>",
    )


@pytest.mark.unit
def test_distributed_helpers_normalize_batches_and_readiness(tmp_path: Path) -> None:
    assert _batches(("hims", " HIMS", "zeta"), 1) == (("HIMS",), ("ZETA",))
    assert _service_name(AnalysisHorizon.LONG_TERM) == "long-term-v2"
    assert _service_name(AnalysisHorizon.SWING) == "swing-v2"
    assert _service_name(AnalysisHorizon.INTRADAY) == "intraday-v2"

    ready_path = tmp_path / "status" / "worker.ready.json"
    _write_ready(ready_path, {"service": "swing-v2", "ready": True})

    assert json.loads(ready_path.read_text(encoding="utf-8")) == {
        "ready": True,
        "service": "swing-v2",
    }


@pytest.mark.unit
def test_weekly_completion_uses_new_york_market_week() -> None:
    bar_timestamp = datetime(2026, 7, 20, 4, tzinfo=UTC)

    assert not _weekly_bar_is_complete(
        bar_timestamp, datetime(2026, 7, 24, 20, tzinfo=UTC)
    )
    assert _weekly_bar_is_complete(
        bar_timestamp, datetime(2026, 7, 25, 4, tzinfo=UTC)
    )


@pytest.mark.unit
async def test_health_is_published_as_a_stable_contract() -> None:
    publisher = RecordingPublisher()
    now = datetime(2026, 7, 28, 14, 30, tzinfo=UTC)

    await _publish_health(publisher, "swing-v2", {"symbols": 2}, now)

    subject, envelope = publisher.events[0]
    assert subject == "marketbot.v1.service.health.swing-v2"
    assert envelope.event_type == SERVICE_HEALTH_EVENT
    assert isinstance(envelope.payload, ServiceHealth)
    assert envelope.payload.service == "swing-v2"


@pytest.mark.unit
def test_worker_factory_selects_only_distributed_analytical_engines() -> None:
    publisher = RecordingPublisher()

    assert isinstance(_build_worker(AnalysisHorizon.LONG_TERM, publisher), LongTermWorker)
    assert isinstance(_build_worker(AnalysisHorizon.SWING, publisher), SwingWorker)
    assert isinstance(_build_worker(AnalysisHorizon.INTRADAY, publisher), IntradayWorker)
    with pytest.raises(ValueError, match="unsupported"):
        _build_worker(AnalysisHorizon.DILUTION, publisher)
