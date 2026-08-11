from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import app.integration.alert_decision_state_store as store_module
from app.alert_engine import AlertEngineV3State
from app.alert_engine.state import SwingContinuationCandidate, SwingContinuationSession
from app.contracts import (
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    PatternDirection,
    new_uuid7,
)
from app.integration.alert_decision_state_store import PostgresAlertDecisionStateStore

NOW = datetime(2026, 8, 9, 12, tzinfo=UTC)


def analysis(symbol: str, horizon: AnalysisHorizon, *, score: str = "80") -> dict[str, object]:
    return AnalysisResult(
        analysis_id=new_uuid7(),
        as_of=NOW,
        confidence=Decimal("0.8"),
        context_hash=f"sha256:{'a' * 64}",
        direction=PatternDirection.BULLISH,
        engine_id=horizon.value.lower(),
        engine_version="1.0.0",
        horizon=horizon,
        metrics=(),
        reasons=("test",),
        score=Decimal(score),
        source_event_ids=(),
        symbol=symbol,
        verdict=AnalysisVerdict.WATCH,
    ).model_dump(mode="json")


class FakeLegacyRepository:
    def __init__(self) -> None:
        self.record: object | None = None

    async def load(self, engine_name: str, implementation_version: str) -> object | None:
        assert engine_name == "alert"
        assert implementation_version == "3.2.0"
        return self.record


class FakeNormalizedRepository:
    def __init__(self) -> None:
        self.analyses: dict[tuple[str, str], object] = {}
        self.candidates: dict[str, object] = {}
        self.sessions: dict[str, object] = {}
        self.analysis_batches: list[tuple[dict[str, object], ...]] = []
        self.candidate_batches: list[tuple[dict[str, object], ...]] = []
        self.session_batches: list[tuple[dict[str, object], ...]] = []

    async def load_analyses(self, *_: object) -> tuple[object, ...]:
        return tuple(self.analyses.values())

    async def load_candidates(self, *_: object) -> tuple[object, ...]:
        return tuple(item for item in self.candidates.values() if item.active)

    async def load_sessions(self, *_: object) -> tuple[object, ...]:
        return tuple(self.sessions.values())

    async def upsert_analyses(self, **values: object) -> None:
        batch = values["values"]
        assert isinstance(batch, tuple)
        self.analysis_batches.append(batch)
        for value in batch:
            self.analyses[(value["symbol"], value["horizon"])] = SimpleNamespace(**value)

    async def upsert_candidates(self, **values: object) -> None:
        batch = values["values"]
        assert isinstance(batch, tuple)
        self.candidate_batches.append(batch)
        for value in batch:
            self.candidates[value["symbol"]] = SimpleNamespace(**value)

    async def upsert_sessions(self, **values: object) -> None:
        batch = values["values"]
        assert isinstance(batch, tuple)
        self.session_batches.append(batch)
        for value in batch:
            self.sessions[value["symbol"]] = SimpleNamespace(**value)


def install_fake_uow(
    monkeypatch: pytest.MonkeyPatch,
    normalized: FakeNormalizedRepository,
    legacy: FakeLegacyRepository,
) -> None:
    class FakeUnitOfWork:
        async def __aenter__(self) -> FakeUnitOfWork:
            self.alert_decision_states = normalized
            self.engine_decision_states = legacy
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(store_module, "PersistenceUnitOfWork", lambda _: FakeUnitOfWork())


@pytest.mark.unit
async def test_postgres_alert_state_store_round_trips_normalized_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    normalized = FakeNormalizedRepository()
    legacy = FakeLegacyRepository()
    install_fake_uow(monkeypatch, normalized, legacy)
    store = PostgresAlertDecisionStateStore(MagicMock(), implementation_version="3.2.0")
    candidate = SwingContinuationCandidate(
        symbol="MNST",
        analysis_id=new_uuid7(),
        observed_at=NOW,
        market_session=NOW.date(),
    )
    state = AlertEngineV3State(
        latest_analyses=(analysis("MNST", AnalysisHorizon.SWING),),
        continuation_candidates=(candidate,),
        continuation_sessions=(
            SwingContinuationSession(symbol="VLO", market_session=NOW.date()),
        ),
    )

    await store.save(state)
    restored = await store.load()

    assert restored == state
    assert len(normalized.analyses) == 1
    assert len(normalized.candidates) == 1
    assert len(normalized.sessions) == 1


@pytest.mark.unit
async def test_postgres_alert_state_store_round_trips_volume_structure_analysis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    normalized = FakeNormalizedRepository()
    legacy = FakeLegacyRepository()
    install_fake_uow(monkeypatch, normalized, legacy)
    store = PostgresAlertDecisionStateStore(MagicMock(), implementation_version="3.2.0")
    state = AlertEngineV3State(
        latest_analyses=(analysis("VLO", AnalysisHorizon.VOLUME_STRUCTURE),)
    )

    await store.save(state)
    restored = await store.load()

    assert restored == state
    assert normalized.analysis_batches[-1][0]["horizon"] == "VOLUME_STRUCTURE"


@pytest.mark.unit
async def test_postgres_alert_state_store_writes_only_changed_symbol_horizon(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    normalized = FakeNormalizedRepository()
    legacy = FakeLegacyRepository()
    install_fake_uow(monkeypatch, normalized, legacy)
    store = PostgresAlertDecisionStateStore(MagicMock(), implementation_version="3.2.0")
    swing = analysis("MNST", AnalysisHorizon.SWING)
    long_term = analysis("MNST", AnalysisHorizon.LONG_TERM)
    initial = AlertEngineV3State(latest_analyses=(long_term, swing))

    assert await store.save_if_changed(initial) is True
    assert await store.save_if_changed(initial) is False

    changed_swing = analysis("MNST", AnalysisHorizon.SWING, score="81")
    changed = AlertEngineV3State(latest_analyses=(long_term, changed_swing))
    assert await store.save_if_changed(changed) is True

    assert len(normalized.analysis_batches) == 2
    assert len(normalized.analysis_batches[0]) == 2
    assert len(normalized.analysis_batches[1]) == 1
    assert normalized.analysis_batches[1][0]["symbol"] == "MNST"
    assert normalized.analysis_batches[1][0]["horizon"] == "SWING"


@pytest.mark.unit
async def test_postgres_alert_state_store_tombstones_removed_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    normalized = FakeNormalizedRepository()
    legacy = FakeLegacyRepository()
    install_fake_uow(monkeypatch, normalized, legacy)
    store = PostgresAlertDecisionStateStore(MagicMock(), implementation_version="3.2.0")
    candidate = SwingContinuationCandidate(
        symbol="MNST",
        analysis_id=new_uuid7(),
        observed_at=NOW,
        market_session=NOW.date(),
    )

    await store.save_if_changed(AlertEngineV3State(continuation_candidates=(candidate,)))
    await store.save_if_changed(AlertEngineV3State())

    assert normalized.candidate_batches[-1] == (
        {"symbol": "MNST", "active": False, "payload": {}},
    )


@pytest.mark.unit
async def test_postgres_alert_state_store_migrates_legacy_checkpoint_on_first_flush(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    normalized = FakeNormalizedRepository()
    legacy = FakeLegacyRepository()
    install_fake_uow(monkeypatch, normalized, legacy)
    state = AlertEngineV3State(
        latest_analyses=(analysis("MNST", AnalysisHorizon.INTRADAY),)
    )
    legacy.record = SimpleNamespace(
        implementation_version="3.2.0",
        state_schema_version="1.0.0",
        payload=state.model_dump(mode="json"),
    )
    store = PostgresAlertDecisionStateStore(MagicMock(), implementation_version="3.2.0")

    assert await store.load() == state
    assert await store.save_if_changed(state) is True
    assert len(normalized.analyses) == 1
