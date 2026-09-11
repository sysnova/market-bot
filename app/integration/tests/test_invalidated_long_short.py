from datetime import timedelta
from decimal import Decimal
from io import StringIO
from pathlib import Path

import pytest

from app.contracts import LOCAL_ALERT_EVENT, EventEnvelope, MarketBar
from app.integration import confirmed_buy_monitor
from app.integration.engine_assembly import EngineSlot, MarketBotAssembly
from app.integration.tests import test_confirmed_buy_monitor as monitor_fixture
from app.intraday_engine.models import IntradayContext
from app.intraday_engine.tests.test_v6 import _ema20_extended_local_breakdown
from app.swing_engine.tests.test_v6 import _bar, _context, _old_failed_breakout

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("final_move, confirmed", [("-0.50", True), ("-0.25", False)])
@pytest.mark.parametrize(
    "definition,symbol", [("7.47.0", "TEST"), ("7.58.0", "ASTS"), ("7.58.0", "NBIS")]
)
async def test_invalidated_long_reaches_short_alert_only_with_mature_intraday(
    final_move: str,
    confirmed: bool,
    definition: str,
    symbol: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assembly = MarketBotAssembly.from_path(ROOT / f"configs/marketbot/{definition}.yaml")
    previous = MarketBotAssembly.from_path(ROOT / "configs/marketbot/7.46.0.yaml")
    bars = _old_failed_breakout(count=60)
    bars[45] = _bar(45, close="98.90", high="99.20", low="98.50")
    base_context = _context(bars)
    swing_context = base_context.model_copy(
        update={
            "price": Decimal("90"),
            "symbol": symbol,
            "daily_bars": tuple(bar.model_copy(update={"symbol": symbol}) for bar in bars),
            "intraday_bars": tuple(
                bar.model_copy(update={"symbol": symbol}) for bar in base_context.intraday_bars
            ),
        }
    )
    source = _ema20_extended_local_breakdown(final_move=final_move)
    factor = swing_context.price / source.minute_bars[-1].close
    offset = swing_context.as_of - source.as_of

    def transform(bar: MarketBar) -> MarketBar:
        return bar.model_copy(
            update={
                "symbol": swing_context.symbol,
                "timestamp": bar.timestamp + offset,
                "open": bar.open * factor,
                "high": bar.high * factor,
                "low": bar.low * factor,
                "close": bar.close * factor,
                "vwap": bar.vwap * factor if bar.vwap is not None else None,
            }
        )

    intraday_context = IntradayContext(
        symbol=swing_context.symbol,
        as_of=swing_context.as_of,
        minute_bars=tuple(transform(bar) for bar in source.minute_bars),
        five_minute_bars=tuple(transform(bar) for bar in source.five_minute_bars),
    )
    intraday = assembly.build_intraday().analyze(intraday_context)
    swing = assembly.build_swing().analyze(swing_context)
    now = swing.as_of + timedelta(minutes=1)
    old_alert_engine = previous.build_alert()
    old_alert_engine.ingest(previous.build_swing().analyze(swing_context), now=now)
    old_alert = old_alert_engine.ingest(intraday, now=now)
    assert old_alert is None or "short_entry_confirmed" not in old_alert.reasons

    alert_engine = assembly.build_alert()
    alert_engine.ingest(swing, now=now)
    alert = alert_engine.ingest(intraday, now=now)
    metrics = {item.name: item.value for item in swing.metrics}
    assert metrics["failed_breakout_state"] == "STRUCTURE_INVALIDATED"
    assert metrics["short_structure_gate_passed"] is True
    if confirmed:
        assert alert is not None
        assert alert.title == f"{symbol} SHORT CONFIRMED"
        assert "short_entry_confirmed" in alert.reasons
        levels = {item.name: item.value for item in alert.metrics}
        assert levels["short_invalidation"] > levels["short_entry_price"] > levels["short_target"]
        # Deliver the actual engine result to the visible monitor's subscription.
        event = EventEnvelope(
            event_type=LOCAL_ALERT_EVENT,
            occurred_at=now,
            source="test",
            subject=symbol,
            payload=alert,
        )
        monkeypatch.setattr(
            confirmed_buy_monitor, "NatsJetStreamEventBus", monitor_fixture._MonitorBus
        )
        monkeypatch.setattr(confirmed_buy_monitor.asyncio, "Event", monitor_fixture._StopEvent)
        monkeypatch.setattr(
            monitor_fixture,
            "_events_for",
            lambda subject: (event,) if subject == "marketbot.v1.alert.local.>" else (),
        )
        sounds: list[bool] = []
        monkeypatch.setattr(
            confirmed_buy_monitor, "play_solid_buy_sound", lambda **_: sounds.append(True)
        )
        output = StringIO()
        with pytest.raises(RuntimeError, match="stop monitor"):
            await confirmed_buy_monitor.run_confirmed_buy_monitor(
                stream=output, bell=True, leveraged_pairs=assembly.build_leveraged_thesis().pairs
            )
        assert f"{symbol} SHORT CONFIRMED" in output.getvalue()
        assert sounds == [True]
    else:
        assert alert is None or "short_entry_confirmed" not in alert.reasons


def test_new_definition_changes_only_swing_and_keeps_rollback() -> None:
    previous = MarketBotAssembly.from_path(ROOT / "configs/marketbot/7.46.0.yaml")
    current = MarketBotAssembly.from_path(ROOT / "configs/marketbot/7.47.0.yaml")
    assert previous.build_swing().engine_version == "14.0.0"
    assert current.build_swing().engine_version == "15.0.0"
    assert current.spec(EngineSlot.SWING).strategy.version == "3.5.0"
    assert current.build_swing()._short_minimum_sma50_break_percent == Decimal("2")
    for slot in current.definition.engines:
        if slot is not EngineSlot.SWING:
            assert current.spec(slot) == previous.spec(slot)
