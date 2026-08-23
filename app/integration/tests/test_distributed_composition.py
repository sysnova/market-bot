import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.common.settings import AppSettings
from app.contracts import (
    SERVICE_HEALTH_EVENT,
    AnalysisHorizon,
    BarTimeframe,
    EventEnvelope,
    ServiceHealth,
)
from app.integration.distributed_composition import (
    _alert_durable_name,
    _build_worker,
    _entry_watcher_subscription_options,
    _horizon_durable_name,
    _microstructure_symbols,
    _publish_health,
    _service_name,
    _write_ready,
    engine_history_requests,
    engine_live_subjects,
    market_stream_subscription_options,
)
from app.integration.engine_assembly import MarketBotAssembly
from app.integration.intraday_worker import IntradayWorker
from app.integration.long_term_worker import LongTermWorker
from app.integration.swing_worker import SwingWorker
from app.intraday_engine import IntradayEngineV3, IntradayEngineV4
from app.market_history_engine.service import _batches, _weekly_bar_is_complete
from app.swing_engine import SwingEngineV3


class RecordingPublisher:
    def __init__(self) -> None:
        self.events: list[tuple[str, EventEnvelope]] = []

    async def publish(self, subject: str, envelope: EventEnvelope) -> None:
        self.events.append((subject, envelope))


DEFINITION = Path(__file__).resolve().parents[3] / "configs/marketbot/4.0.0.yaml"


def _assembly(rule_version: str | None = None) -> MarketBotAssembly:
    return MarketBotAssembly.from_settings(
        AppSettings(
            definition_path=DEFINITION,
            entry_confirmation_rule_version=rule_version,
            _env_file=None,
        )
    )


@pytest.mark.unit
def test_each_engine_requests_only_its_own_historical_context() -> None:
    long = engine_history_requests(AnalysisHorizon.LONG_TERM)
    swing = engine_history_requests(AnalysisHorizon.SWING)
    intraday = engine_history_requests(AnalysisHorizon.INTRADAY)

    assert [(item.timeframe, item.max_bars_per_symbol) for item in long] == [
        (BarTimeframe.DAY_1, 260),
        (BarTimeframe.WEEK_1, 220),
        (BarTimeframe.MINUTE_1, 500),
    ]
    assert [(item.timeframe, item.max_bars_per_symbol) for item in swing] == [
        (BarTimeframe.DAY_1, 120),
        (BarTimeframe.MINUTE_15, 160),
        (BarTimeframe.MINUTE_1, 500),
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
    assert engine_live_subjects(AnalysisHorizon.INTRADAY) == ("marketbot.v1.market.bar.1Min.>",)


@pytest.mark.unit
def test_market_stream_subscribes_only_to_events_consumed_by_engines() -> None:
    assert market_stream_subscription_options() == {
        "trades": True,
        "quotes": True,
        "bars": True,
        "updated_bars": True,
        "daily_bars": True,
    }


@pytest.mark.unit
def test_microstructure_universe_prioritizes_holdings_then_caps_watchlist() -> None:
    assert _microstructure_symbols(
        ("AAPL", "MSFT", "NVDA", "TSLA"),
        ("TSLA", "RIOT"),
        max_symbols=4,
    ) == ("TSLA", "RIOT", "AAPL", "MSFT")


@pytest.mark.unit
def test_distributed_helpers_normalize_batches_and_readiness(tmp_path: Path) -> None:
    assert _batches(("hims", " HIMS", "zeta"), 1) == (("HIMS",), ("ZETA",))
    assert _service_name(AnalysisHorizon.LONG_TERM) == "long-term"
    assert _service_name(AnalysisHorizon.SWING) == "swing"
    assert _service_name(AnalysisHorizon.INTRADAY) == "intraday"

    ready_path = tmp_path / "status" / "worker.ready.json"
    _write_ready(ready_path, {"service": "swing", "ready": True})

    assert json.loads(ready_path.read_text(encoding="utf-8")) == {
        "ready": True,
        "service": "swing",
    }


@pytest.mark.unit
def test_durable_names_follow_logical_service_and_contract_major() -> None:
    assert _horizon_durable_name(AnalysisHorizon.LONG_TERM, 1) == (
        "marketbot-long-term-market-v1-1"
    )
    assert _horizon_durable_name(AnalysisHorizon.SWING, 2) == ("marketbot-swing-market-v1-2")
    assert _alert_durable_name("analysis") == "marketbot-alert-analysis-v1"
    assert _alert_durable_name("entry-watch") == "marketbot-alert-entry-watch-v1"


@pytest.mark.unit
def test_entry_watcher_subscription_replays_latest_subject_snapshots() -> None:
    options = _entry_watcher_subscription_options()

    assert options.durable_name is None
    assert options.replay_all is False
    assert options.replay_latest_per_subject is True
    assert options.ack_wait_seconds == 60


@pytest.mark.unit
def test_weekly_completion_uses_new_york_market_week() -> None:
    bar_timestamp = datetime(2026, 7, 20, 4, tzinfo=UTC)

    assert not _weekly_bar_is_complete(bar_timestamp, datetime(2026, 7, 24, 20, tzinfo=UTC))
    assert _weekly_bar_is_complete(bar_timestamp, datetime(2026, 7, 25, 4, tzinfo=UTC))


@pytest.mark.unit
async def test_health_is_published_as_a_stable_contract() -> None:
    publisher = RecordingPublisher()
    now = datetime(2026, 7, 28, 14, 30, tzinfo=UTC)

    await _publish_health(publisher, "swing", {"symbols": 2}, now)

    subject, envelope = publisher.events[0]
    assert subject == "marketbot.v1.service.health.swing"
    assert envelope.event_type == SERVICE_HEALTH_EVENT
    assert isinstance(envelope.payload, ServiceHealth)
    assert envelope.payload.service == "swing"


@pytest.mark.unit
def test_worker_factory_selects_only_distributed_analytical_engines() -> None:
    publisher = RecordingPublisher()

    assembly = _assembly()
    assert isinstance(
        _build_worker(AnalysisHorizon.LONG_TERM, publisher, assembly=assembly), LongTermWorker
    )
    assert isinstance(
        _build_worker(AnalysisHorizon.SWING, publisher, assembly=assembly), SwingWorker
    )
    assert isinstance(
        _build_worker(AnalysisHorizon.INTRADAY, publisher, assembly=assembly), IntradayWorker
    )
    with pytest.raises(ValueError, match="unsupported"):
        _build_worker(AnalysisHorizon.DILUTION, publisher, assembly=assembly)


@pytest.mark.unit
def test_worker_factory_selects_exact_v3_rule_artifact() -> None:
    publisher = RecordingPublisher()
    assembly = _assembly("3.0.0")
    swing = _build_worker(AnalysisHorizon.SWING, publisher, assembly=assembly)
    intraday = _build_worker(AnalysisHorizon.INTRADAY, publisher, assembly=assembly)

    assert isinstance(swing._analyzer, SwingEngineV3)
    assert isinstance(intraday._analyzer, IntradayEngineV3)


@pytest.mark.unit
def test_worker_factory_selects_v4_intraday_and_preserves_v3_swing() -> None:
    publisher = RecordingPublisher()
    assembly = _assembly("4.0.0")
    swing = _build_worker(AnalysisHorizon.SWING, publisher, assembly=assembly)
    intraday = _build_worker(AnalysisHorizon.INTRADAY, publisher, assembly=assembly)

    assert isinstance(swing._analyzer, SwingEngineV3)
    assert isinstance(intraday._analyzer, IntradayEngineV4)
