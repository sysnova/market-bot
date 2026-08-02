from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.contracts import BarTimeframe, MarketBar, WavePhase
from app.elliott_wave_engine import ElliottWaveEngine, WaveContext

NOW = datetime(2026, 8, 1, 20, tzinfo=UTC)


def _bars(prices: tuple[int, ...]) -> tuple[MarketBar, ...]:
    return tuple(
        MarketBar(
            symbol="TGT",
            timeframe=BarTimeframe.DAY_1,
            timestamp=NOW - timedelta(days=len(prices) - index),
            open=Decimal(price),
            high=Decimal(price + 1),
            low=Decimal(price - 1),
            close=Decimal(price),
            volume=Decimal(1_000 + index * 50),
            source="fixture",
            feed="test",
        )
        for index, price in enumerate(prices)
    )


def test_detects_wave_2_ending_with_wave_3_plan() -> None:
    prices = (
        100, 98, 94, 91, 90, 94, 100, 108, 115, 120,
        116, 108, 100, 102, 106, 109,
    )

    result = ElliottWaveEngine().evaluate(WaveContext(symbol="TGT", daily_bars=_bars(prices)))

    assert result.phase is WavePhase.WAVE_2_ENDING
    assert result.primary_timeframe is BarTimeframe.DAY_1
    assert result.wave1_origin is not None
    assert result.wave1_peak is not None
    assert result.corrective_low is not None
    assert result.entry_zone_low < result.entry_zone_high
    assert result.invalidation < result.entry_zone_low
    assert result.target_low > result.current_price
    assert result.target_high >= result.target_low
    assert result.confidence >= Decimal("0.60")


def test_detects_wave_4_ending_without_weakening_existing_engines() -> None:
    prices = (
        100, 96, 92, 90, 94, 101, 108, 110,
        106, 101, 98, 102, 110, 120, 130,
        127, 122, 119, 121, 124, 126,
    )

    result = ElliottWaveEngine().evaluate(WaveContext(symbol="TGT", daily_bars=_bars(prices)))

    assert result.phase is WavePhase.WAVE_4_ENDING
    assert result.corrective_low > result.wave1_peak
    assert result.invalidation <= result.wave1_peak
    assert any(reason == "wave4_retracement_in_0236_0500" for reason in result.reasons)


def test_reports_unresolved_structure_instead_of_forcing_a_count() -> None:
    prices = tuple(100 + (index % 3) for index in range(70))

    result = ElliottWaveEngine().evaluate(WaveContext(symbol="TGT", daily_bars=_bars(prices)))

    assert result.phase is WavePhase.UNRESOLVED
    assert result.score <= Decimal("35")
    assert result.entry_zone_low is None
    assert "no_valid_impulse_count" in result.reasons


def test_does_not_keep_an_active_count_after_its_targets_are_exhausted() -> None:
    prices = (
        100, 98, 94, 91, 90, 94, 100, 108, 115, 120,
        116, 108, 100, 102, 106, 109, 160, 180, 200,
    )

    result = ElliottWaveEngine().evaluate(WaveContext(symbol="TGT", daily_bars=_bars(prices)))

    assert not (
        result.phase is WavePhase.WAVE_3_ACTIVE
        and result.target_high is not None
        and result.current_price > result.target_high
    )
