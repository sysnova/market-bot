"""Public market-rotation report contract."""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field

from ._base import StrictFrozenModel


class RotationSector(StrictFrozenModel):
    code: str
    label: str
    proxy: str
    score: Decimal = Field(ge=0, le=100)
    state: Literal["INFLOW", "ACCUMULATING", "NEUTRAL", "OUTFLOW", "DEFENSIVE_ROTATION"]
    top_symbols: tuple[str, ...] = ()


class MarketRotationReport(StrictFrozenModel):
    run_id: str
    generated_at: datetime
    risk_regime: str
    sectors: tuple[RotationSector, ...]
    watchlist_additions: tuple[str, ...] = ()
