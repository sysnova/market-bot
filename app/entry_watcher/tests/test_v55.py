from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from app.contracts import (
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    NamedValue,
    PatternDirection,
)
from app.entry_watcher import EntryWatcherV55, InMemoryEntryWatchStore

NOW = datetime(2026, 8, 22, 15, tzinfo=UTC)
HASH = "sha256:" + "a" * 64


def _analysis(
    horizon: AnalysisHorizon,
    *,
    classification: str,
    extra_metrics: tuple[NamedValue, ...] = (),
) -> AnalysisResult:
    return AnalysisResult(
        analysis_id=UUID(
            {
                AnalysisHorizon.LONG_TERM: "0195f3a5-9000-7000-8000-000000000011",
                AnalysisHorizon.SWING: "0195f3a5-9000-7000-8000-000000000012",
                AnalysisHorizon.INTRADAY: "0195f3a5-9000-7000-8000-000000000013",
            }[horizon]
        ),
        engine_id=f"fixture-{horizon.value.lower()}",
        engine_version="1.0.0",
        symbol="TEST",
        horizon=horizon,
        as_of=NOW,
        verdict=AnalysisVerdict.FAVORABLE,
        direction=PatternDirection.BULLISH,
        score=Decimal("80"),
        confidence=Decimal("0.8"),
        reasons=("fixture",),
        metrics=(
            NamedValue(name="classification", value=classification),
            NamedValue(name="reference_price", value=Decimal("100")),
            *extra_metrics,
        ),
        context_hash=HASH,
    )


@pytest.mark.unit
def test_v55_accepts_versioned_structure_recovery_confirmation() -> None:
    watcher = EntryWatcherV55(store=InMemoryEntryWatchStore())
    analyses = {
        AnalysisHorizon.LONG_TERM: _analysis(
            AnalysisHorizon.LONG_TERM,
            classification="buy_zone",
        ),
        AnalysisHorizon.SWING: _analysis(
            AnalysisHorizon.SWING,
            classification="recovery",
            extra_metrics=(
                NamedValue(name="anchored_vwap_gate_passed", value=True),
                NamedValue(name="entry_lane", value="STRUCTURE_RECOVERY"),
                NamedValue(name="recovery_entry_gate_passed", value=True),
            ),
        ),
        AnalysisHorizon.INTRADAY: _analysis(
            AnalysisHorizon.INTRADAY,
            classification="vwap_reclaim",
            extra_metrics=(
                NamedValue(name="confirmation_gate_passed", value=True),
                NamedValue(name="mature_confirmation_gate_passed", value=True),
                NamedValue(name="entry_efficiency_gate_passed", value=True),
                NamedValue(name="confirmation_quality", value="strong"),
                NamedValue(name="five_minute_higher_low", value=True),
            ),
        ),
    }

    assert watcher._confirmed(analyses, now=NOW) is True
