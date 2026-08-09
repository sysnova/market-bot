from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import app.integration.alert_decision_state_store as store_module
from app.alert_engine import AlertEngineV3State
from app.integration.alert_decision_state_store import PostgresAlertDecisionStateStore

NOW = datetime(2026, 8, 9, 12, tzinfo=UTC)


class FakeRepository:
    def __init__(self) -> None:
        self.record: object | None = None
        self.expected_implementation = "3.2.0"

    async def load(
        self, engine_name: str, implementation_version: str
    ) -> object | None:
        assert engine_name == "alert"
        assert implementation_version == self.expected_implementation
        return self.record

    async def upsert(self, **values: object) -> None:
        self.record = SimpleNamespace(**values, updated_at=NOW)


@pytest.mark.unit
async def test_postgres_alert_state_store_round_trips_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = FakeRepository()

    class FakeUnitOfWork:
        async def __aenter__(self) -> FakeUnitOfWork:
            self.engine_decision_states = repository
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(store_module, "PersistenceUnitOfWork", lambda _: FakeUnitOfWork())
    store = PostgresAlertDecisionStateStore(
        MagicMock(), implementation_version="3.2.0"
    )
    state = AlertEngineV3State()

    await store.save(state)
    restored = await store.load()

    assert restored == state
    assert repository.record.implementation_version == "3.2.0"  # type: ignore[union-attr]
