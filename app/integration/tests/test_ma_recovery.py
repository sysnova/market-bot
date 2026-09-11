# pyright: reportPrivateUsage=false
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from app.contracts import BarTimeframe, GeriAssessment, MarketBar
from app.integration.engine_assembly import MarketBotAssembly
from app.swing_4h_geri_engine.daily_recovery_levels import average_snapshot
from app.swing_4h_geri_engine.models import Swing4HGeriContext
from app.swing_4h_geri_engine.tests.test_recovery import context
from app.swing_4h_geri_engine.v111 import Swing4HGeriEngineV111


def engine() -> Swing4HGeriEngineV111:
    built = MarketBotAssembly.from_path(
        Path(__file__).resolve().parents[3] / "configs/marketbot/7.53.0.yaml"
    ).build_4hgeri()
    assert isinstance(built, Swing4HGeriEngineV111)
    return built


def ma_context() -> Swing4HGeriContext:
    c = context()
    daily = tuple(
        c.bars[0].model_copy(
            update={
                "timeframe": BarTimeframe.DAY_1,
                "timestamp": c.bars[0].timestamp.replace(hour=4) - timedelta(days=60 - i),
                "open": Decimal("115"),
                "close": Decimal("115"),
                "low": Decimal("110"),
                "high": Decimal("120"),
            }
        )
        for i in range(60)
    )
    baseline = tuple(
        b.model_copy(
            update={
                "timestamp": b.timestamp - timedelta(days=days),
                "volume": Decimal("500"),
            }
        )
        for days in range(7, 0, -1)
        for b in c.confirmation_bars
    )
    return replace(c, daily_bars=daily, confirmation_bars=(*baseline, *c.confirmation_bars))


def test_daily_mean_is_mandatory_target_and_old_minor_levels_are_exposed() -> None:
    item = engine().analyze(ma_context())
    m = {v.name: v.value for v in item.metrics}
    assert item.engine_version == "1.11.0"
    assert m["countertrend_target"] == Decimal("115")
    assert m["countertrend_target_source"] == "EMA21_DAILY"
    assert m["countertrend_entry_rvol"] == Decimal("2")
    assert m["countertrend_eligible"] is True
    assert Decimal("110") in m["countertrend_intermediate_horizontal_levels"]


def test_insufficient_rr_to_nearest_mean_cannot_use_further_target() -> None:
    c = ma_context()
    daily = tuple(b.model_copy(update={"close": Decimal("101")}) for b in c.daily_bars)
    m = {v.name: v.value for v in engine().analyze(replace(c, daily_bars=daily)).metrics}
    assert m["countertrend_target"] == Decimal("101")
    assert m["countertrend_eligible"] is False
    assert "insufficient_reward_risk_to_daily_ma" in m["countertrend_eligibility_reasons"]


def test_missing_daily_history_and_weak_volume_do_not_buy() -> None:
    c = ma_context()
    for changed in (
        replace(c, daily_bars=c.daily_bars[-20:]),
        replace(
            c,
            confirmation_bars=tuple(
                b.model_copy(update={"volume": Decimal("500")}) for b in c.confirmation_bars
            ),
        ),
    ):
        m = {v.name: v.value for v in engine().analyze(changed).metrics}
        assert m["countertrend_eligible"] is False


def test_closest_mean_is_selected_by_price_not_by_indicator_name() -> None:
    c = ma_context()
    daily: tuple[MarketBar, ...] = tuple(
        b.model_copy(
            update={
                "close": Decimal("80") if i < 40 else Decimal("120"),
            }
        )
        for i, b in enumerate(c.daily_bars)
    )
    assert c.current_price_at is not None
    snapshot = average_snapshot(daily, c.current_price_at)
    assert snapshot is not None and snapshot.sma50 < snapshot.ema21
    # Test via the assembled engine's recovery at a price below both means.
    selected = engine()._target(replace(c, daily_bars=daily), Decimal("90"), c.current_price_at)
    assert selected == snapshot.sma50


def test_window_target_is_frozen_when_daily_history_buffer_rolls() -> None:
    c = ma_context()
    daily = tuple(
        b.model_copy(update={"close": Decimal("105") if i == 0 else Decimal("115")})
        for i, b in enumerate(c.daily_bars)
    )
    c = replace(c, daily_bars=daily)
    model = engine()
    first = model.analyze(c)
    restored = GeriAssessment.model_validate_json(first.model_dump_json())
    later = model.analyze(replace(c, daily_bars=daily[1:], active_structure=restored))
    left, right = ({v.name: v.value for v in a.metrics} for a in (first, later))
    assert left["countertrend_target"] == right["countertrend_target"]
    assert left["countertrend_invalidation"] == right["countertrend_invalidation"]
    assert (
        right["countertrend_target_source"] == left["countertrend_target_source"] == "EMA21_DAILY"
    )


def test_observation_before_acceptance_uses_same_daily_target_policy() -> None:
    c = ma_context()
    item = engine().analyze(
        replace(c, confirmation_bars=c.confirmation_bars[:-1], current_price=Decimal("99.8"))
    )
    m = {v.name: v.value for v in item.metrics}
    assert m["countertrend_maturity"] == "CT1"
    assert m["countertrend_fast_confirmation"] is False
    assert m["countertrend_target"] == Decimal("115")


def test_nearer_recent_daily_resistance_caps_target_before_mean() -> None:
    c = ma_context()
    daily = (
        *c.daily_bars[:-3],
        *(
            b.model_copy(
                update={
                    "close": Decimal("100"),
                    "open": Decimal("100"),
                    "low": Decimal("90"),
                    "high": Decimal(high),
                }
            )
            for b, high in zip(c.daily_bars[-3:], ("108", "109", "108"), strict=True)
        ),
    )
    item = engine().analyze(replace(c, daily_bars=daily))
    m = {v.name: v.value for v in item.metrics}
    assert m["countertrend_target"] == Decimal("109")
    assert m["countertrend_target_source"] == "RECENT_DAILY_PIVOT_HIGH"
