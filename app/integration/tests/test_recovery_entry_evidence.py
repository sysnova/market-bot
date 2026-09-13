from datetime import timedelta
from decimal import Decimal

from app.alert_engine.v310 import AlertEngineV310
from app.contracts import AnalysisVerdict, EntrySignal
from app.entry_opportunity_engine import EntryOpportunityEngineV20, InMemoryEntryOpportunityStore
from app.integration.entry_signal_adapter import entry_signal_from_alert
from app.swing_engine import SwingEngineV16
from app.swing_engine.tests.test_v10 import _context


async def test_confirming_swing_evidence_survives_alert_signal_serialization_and_persistence() -> (
    None
):
    context = _context()
    swing = SwingEngineV16(recovery_maximum_risk_atr=Decimal("10")).analyze(context)
    now = context.as_of + timedelta(minutes=15)
    alert = AlertEngineV310().ingest(swing, now=now)
    assert alert is not None
    signal = entry_signal_from_alert(alert)
    assert signal is not None
    signal = EntrySignal.model_validate_json(signal.model_dump_json())
    assert signal.entry_analyses[0].analysis_id == swing.analysis_id
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV20(store=store)
    await engine.ingest_signal(signal)
    active = await store.load_active(context.symbol)
    assert active is not None
    cp = active.checkpoints[0]
    assert cp.entry_analyses == signal.entry_analyses
    assert cp.recovery_exit is not None
    assert cp.recovery_exit.analysis_id == swing.analysis_id
    assert cp.recovery_exit.breakout_level < cp.entry_price
    # Later WATCH data may update the live analysis but never the entry evidence.
    later = swing.model_copy(
        update={"as_of": now + timedelta(minutes=15), "verdict": AnalysisVerdict.WATCH}
    )
    await engine.ingest_analysis(later, now=now + timedelta(minutes=15))
    after = await store.load_latest(context.symbol)
    assert after is not None
    assert after.checkpoints[0].entry_analyses == cp.entry_analyses
    assert after.checkpoints[0].recovery_exit == cp.recovery_exit
