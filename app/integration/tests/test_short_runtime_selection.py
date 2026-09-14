"""Regression for the SHORT correction omitted from the deployed assembly."""

from datetime import timedelta
from decimal import Decimal
from io import StringIO
from pathlib import Path

import pytest

from app.alert_engine.tests.test_v39 import _swing
from app.contracts import LOCAL_ALERT_EVENT, AnalysisVerdict, EventEnvelope, NamedValue
from app.integration import confirmed_buy_monitor
from app.integration.engine_assembly import EngineSlot, MarketBotAssembly
from app.integration.tests import test_confirmed_buy_monitor as monitor_fixture
from app.intraday_engine.tests.short_replay import contexts

ROOT = Path(__file__).resolve().parents[3]
FIXED = ROOT / "configs/marketbot/7.69.0.yaml"


def test_runtime_fix_preserves_every_other_deployed_engine() -> None:
    previous = MarketBotAssembly.from_path(ROOT / "configs/marketbot/7.60.0.yaml")
    current = MarketBotAssembly.from_path(FIXED)
    assert current.build_intraday().engine_version == "8.0.0"
    assert current.spec(EngineSlot.INTRADAY).strategy.version == "1.4.0"
    assert set(current.definition.engines) == set(previous.definition.engines)
    for slot in previous.definition.engines:
        if slot is not EngineSlot.INTRADAY:
            assert current.spec(slot) == previous.spec(slot)


@pytest.mark.parametrize("symbol", ["ASTS", "NBIS", "ASTN"])
async def test_early_short_replay_reaches_existing_audible_monitor(
    symbol: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # NBIS/ASTN use the same ASTS price path to verify scope, not historical NBIS returns.
    current = MarketBotAssembly.from_path(FIXED)
    previous = MarketBotAssembly.from_path(ROOT / "configs/marketbot/7.60.0.yaml")
    context = next(c for c in contexts() if c.as_of.minute == 14 and c.as_of.hour == 14)
    context = context.model_copy(update={
        "symbol": symbol,
        "minute_bars": tuple(b.model_copy(update={"symbol": symbol}) for b in context.minute_bars),
        "five_minute_bars": tuple(
            b.model_copy(update={"symbol": symbol}) for b in context.five_minute_bars
        ),
    })
    assert previous.build_intraday().analyze(context).verdict is AnalysisVerdict.WATCH
    intraday = current.build_intraday().analyze(context)
    assert intraday.verdict is AnalysisVerdict.FAVORABLE

    swing = _swing().model_copy(update={
        "symbol": symbol,
        "as_of": context.as_of - timedelta(minutes=29),
        "metrics": (
            NamedValue(name="reference_price", value=Decimal("64.643")),
            NamedValue(name="reward_risk_to_resistance", value=Decimal("0.9886")),
            NamedValue(name="short_structure_gate_passed", value=True),
            NamedValue(name="short_setup_id", value=f"swing-short:{symbol}:2026-08-12"),
        ),
    })
    now = context.as_of + timedelta(minutes=1)
    engine = current.build_alert()
    engine.ingest(swing, now=now)
    alert = engine.ingest(intraday, now=now)
    if symbol == "ASTN":
        assert alert is None or "short_entry_confirmed" not in alert.reasons
        return
    assert alert is not None and "short_entry_confirmed" in alert.reasons
    levels = {m.name: m.value for m in alert.metrics}
    assert levels["short_entry_price"] == Decimal("64.255")
    assert levels["short_invalidation"] == Decimal("64.4156")
    assert levels["short_target"] == Decimal("64.0140")
    event = EventEnvelope(
        event_type=LOCAL_ALERT_EVENT, occurred_at=now, source="offline-replay",
        subject=symbol, payload=alert,
    )
    monkeypatch.setattr(confirmed_buy_monitor, "NatsJetStreamEventBus", monitor_fixture._MonitorBus)
    monkeypatch.setattr(confirmed_buy_monitor.asyncio, "Event", monitor_fixture._StopEvent)
    monkeypatch.setattr(
        monitor_fixture, "_events_for",
        lambda subject: (event, event) if subject == "marketbot.v1.alert.local.>" else (),
    )
    sounds: list[bool] = []
    monkeypatch.setattr(
        confirmed_buy_monitor, "play_solid_buy_sound", lambda **_: sounds.append(True),
    )
    output = StringIO()
    with pytest.raises(RuntimeError, match="stop monitor"):
        await confirmed_buy_monitor.run_confirmed_buy_monitor(
            stream=output, bell=True, leveraged_pairs=current.build_leveraged_thesis().pairs,
        )
    assert f"{symbol} SHORT CONFIRMED" in output.getvalue()
    assert "Invalidation $64.4156" in output.getvalue()
    assert "Objective $64.014" in output.getvalue()
    assert sounds == [True]
