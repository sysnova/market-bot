"""Merge the actual active engine requirements into one initial Redis load."""

from app.common.settings import AppSettings
from app.contracts import AnalysisHorizon, BarTimeframe, MarketHistoryRequirement

from .distributed_composition import engine_history_requests
from .elliott_wave_composition import ELLIOTT_HISTORY_REQUESTS
from .engine_assembly import EngineMode, EngineSlot, MarketBotAssembly
from .market_rotation_composition import ROTATION_HISTORY_REQUESTS
from .patreon_caps_composition import PATREON_HISTORY_REQUESTS
from .support_confirmation_composition import SUPPORT_HISTORY_REQUESTS
from .swing_4h_geri_composition import GERI_HISTORY_REQUESTS
from .swing_trade_composition import (
    SWING_TRADE_HISTORY_REQUESTS,
    SWING_TRADE_MOMENTUM_HISTORY_REQUESTS,
)
from .volume_structure_composition import VOLUME_STRUCTURE_HISTORY_REQUESTS


def history_manifest(settings: AppSettings) -> tuple[MarketHistoryRequirement, ...]:
    assembly = MarketBotAssembly.from_settings(settings)
    requests = {
        EngineSlot.LONG_TERM: engine_history_requests(AnalysisHorizon.LONG_TERM),
        EngineSlot.SWING: engine_history_requests(AnalysisHorizon.SWING),
        EngineSlot.INTRADAY: engine_history_requests(AnalysisHorizon.INTRADAY),
        EngineSlot.GERI_4H: GERI_HISTORY_REQUESTS,
        EngineSlot.SWING_TRADE: (
            SWING_TRADE_MOMENTUM_HISTORY_REQUESTS
            if EngineSlot.SWING_TRADE in assembly.definition.engines
            and assembly.spec(EngineSlot.SWING_TRADE).implementation
            in {"1.6.0", "1.7.0", "1.8.0", "1.9.0", "1.10.0", "1.11.0", "1.12.0"}
            else SWING_TRADE_HISTORY_REQUESTS
        ),
        EngineSlot.SUPPORT_CONFIRMATION: SUPPORT_HISTORY_REQUESTS,
        EngineSlot.ELLIOTT_WAVE: ELLIOTT_HISTORY_REQUESTS,
        EngineSlot.PATREON_CAPS: PATREON_HISTORY_REQUESTS,
        EngineSlot.VOLUME_STRUCTURE: VOLUME_STRUCTURE_HISTORY_REQUESTS,
        EngineSlot.MARKET_ROTATION: ROTATION_HISTORY_REQUESTS,
    }
    merged: dict[BarTimeframe, MarketHistoryRequirement] = {}
    for slot, items in requests.items():
        if (
            slot not in assembly.definition.engines
            or assembly.spec(slot).mode is not EngineMode.ACTIVE
        ):
            continue
        for item in items:
            previous = merged.get(item.timeframe, item)
            merged[item.timeframe] = MarketHistoryRequirement(
                timeframe=item.timeframe,
                lookback=max(previous.lookback, item.lookback),
                max_bars_per_symbol=max(previous.max_bars_per_symbol, item.max_bars_per_symbol),
            )
    return tuple(merged.values())
