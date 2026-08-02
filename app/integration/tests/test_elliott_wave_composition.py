from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from app.contracts import WavePhase
from app.integration.elliott_wave_composition import load_held_symbols
from app.integration.elliott_wave_monitor import _format_assessment


class _Universe:
    def __init__(self) -> None:
        self.holdings_calls = 0
        self.universe_calls = 0

    async def get_holdings(self) -> SimpleNamespace:
        self.holdings_calls += 1
        return SimpleNamespace(symbols=("TGT", "NVDA"), source="postgresql-local-holdings")

    async def get_universe(self) -> SimpleNamespace:
        self.universe_calls += 1
        return SimpleNamespace(symbols=("TGT", "NVDA", "WATCH_ONLY"))


async def test_elliott_universe_is_strictly_positive_holdings() -> None:
    provider = _Universe()

    snapshot = await load_held_symbols(provider)

    assert snapshot.symbols == ("TGT", "NVDA")
    assert snapshot.source == "postgresql-local-holdings"
    assert provider.holdings_calls == 1
    assert provider.universe_calls == 0


def test_tmux_launcher_has_a_sibling_elliott_wave_window() -> None:
    launcher = Path("scripts/linux/start-market-bot.sh").read_text(encoding="utf-8")

    assert "-n ElliottWave" in launcher
    assert "--role elliott-wave" in launcher
    assert "ELLIOTT WAVE" in launcher
    assert launcher.index(
        'while [[ ! -f "$STATUS_ROOT/elliott-wave-v0.ready.json" ]]'
    ) < launcher.index("uv run marketbot monitor elliott-wave")


def test_panel_format_names_the_wave_and_critical_levels() -> None:
    item = SimpleNamespace(
        occurred_at=datetime(2026, 8, 1, 20, tzinfo=UTC),
        symbol="TGT",
        phase=WavePhase.WAVE_2_ENDING,
        score=Decimal("78"),
        confidence=Decimal("0.78"),
        current_price=Decimal("105"),
        entry_zone_low=Decimal("98"),
        entry_zone_high=Decimal("105"),
        trigger_price=Decimal("109"),
        invalidation=Decimal("89"),
        target_low=Decimal("139"),
        target_high=Decimal("151"),
    )

    text = _format_assessment(item)

    assert "WAVE_2_ENDING" in text
    assert "Z 98-105" in text
    assert "INV 89" in text
    assert "TGT 139-151" in text
