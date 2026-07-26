from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.contracts import (
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    PatternDirection,
)
from app.dilution_sec_engine import DilutionEvaluationInput
from app.integration.sec_refresher import SecAnalysisRefresher

NOW = datetime(2026, 7, 26, 15, 0, tzinfo=UTC)
HASH = "sha256:" + "d" * 64


class Resolver:
    async def resolve(self, symbol: str) -> str:
        if symbol == "BAD":
            raise LookupError("unknown ticker")
        return "0000320193"


class Loader:
    async def load(
        self, *, cik: str, symbol: str, as_of: date
    ) -> DilutionEvaluationInput:
        assert cik == "0000320193"
        return DilutionEvaluationInput(symbol=symbol, as_of=as_of)


class Engine:
    def evaluate(self, request: DilutionEvaluationInput) -> AnalysisResult:
        return AnalysisResult(
            engine_id="dilution-test",
            engine_version="1.0.0",
            symbol=request.symbol,
            horizon=AnalysisHorizon.DILUTION,
            as_of=datetime.combine(request.as_of, datetime.min.time(), tzinfo=UTC),
            verdict=AnalysisVerdict.FAVORABLE,
            direction=PatternDirection.NEUTRAL,
            score=Decimal("0"),
            confidence=Decimal("1"),
            reasons=("fixture",),
            context_hash=HASH,
        )


class Runtime:
    def __init__(self) -> None:
        self.results: list[AnalysisResult] = []

    async def ingest_analysis(self, result: AnalysisResult) -> None:
        self.results.append(result)


@pytest.mark.unit
async def test_sec_refresh_resolves_each_symbol_and_isolates_failures() -> None:
    runtime = Runtime()
    failures: list[tuple[str, Exception]] = []
    refresher = SecAnalysisRefresher(
        resolver=Resolver(),
        loader=Loader(),
        engine=Engine(),
        runtime=runtime,
        request_spacing_seconds=0,
        on_error=lambda symbol, error: failures.append((symbol, error)),
    )

    await refresher.refresh(("AAPL", "BAD", "MSFT"), NOW)

    assert [result.symbol for result in runtime.results] == ["AAPL", "MSFT"]
    assert failures[0][0] == "BAD"
