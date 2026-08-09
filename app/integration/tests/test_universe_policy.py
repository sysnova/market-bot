import pytest

from app.integration.engine_assembly import EngineSlot
from app.integration.universe_policy import (
    ENGINE_UNIVERSE_POLICIES,
    universe_health_details,
)


@pytest.mark.unit
def test_every_engine_slot_declares_universe_and_warmup_policy() -> None:
    assert set(ENGINE_UNIVERSE_POLICIES) == {slot.value for slot in EngineSlot}
    assert universe_health_details("entry-watcher-v5") == {
        "universe_policy": "derived-from-core-analysis-and-entry-events",
        "warmup_policy": "source-contract-replay-and-durable-state-restore",
    }
