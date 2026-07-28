"""Scheduled SEC analysis kept outside the realtime market tick path."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

from app.contracts import AnalysisResult
from app.dilution_sec_engine import DilutionEvaluationInput


class TickerResolver(Protocol):
    async def resolve(self, symbol: str) -> str: ...


class SecSnapshotLoader(Protocol):
    async def load(
        self,
        *,
        cik: str,
        symbol: str,
        as_of: date,
    ) -> DilutionEvaluationInput: ...


class DilutionAnalyzer(Protocol):
    def evaluate(self, request: DilutionEvaluationInput) -> AnalysisResult: ...


class AnalysisConsumer(Protocol):
    async def ingest_analysis(self, result: AnalysisResult) -> None: ...


SecRefreshErrorHandler = Callable[[str, Exception], None]


@dataclass(frozen=True, slots=True)
class SecRefreshSummary:
    symbols_scanned: int
    symbols_with_filings: int
    filings_analyzed: int
    analyses_published: int
    failures: int


class SecAnalysisRefresher:
    """Resolve, fetch and evaluate SEC inputs on an explicit slow cadence."""

    def __init__(
        self,
        *,
        resolver: TickerResolver,
        loader: SecSnapshotLoader,
        engine: DilutionAnalyzer,
        runtime: AnalysisConsumer,
        request_spacing_seconds: float = 0.25,
        on_error: SecRefreshErrorHandler | None = None,
        skip_without_filings: bool = False,
    ) -> None:
        if request_spacing_seconds < 0:
            raise ValueError("request_spacing_seconds cannot be negative")
        self._resolver = resolver
        self._loader = loader
        self._engine = engine
        self._runtime = runtime
        self._spacing = request_spacing_seconds
        self._on_error = on_error
        self._skip_without_filings = skip_without_filings

    async def refresh(
        self, symbols: tuple[str, ...], as_of: datetime
    ) -> SecRefreshSummary:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("SEC refresh time must be timezone-aware")
        normalized = tuple(dict.fromkeys(symbol.strip().upper() for symbol in symbols))
        symbols_with_filings = 0
        filings_analyzed = 0
        analyses_published = 0
        failures = 0
        for index, symbol in enumerate(normalized):
            try:
                cik = await self._resolver.resolve(symbol)
                request = await self._loader.load(
                    cik=cik,
                    symbol=symbol,
                    as_of=as_of.date(),
                )
                if not self._skip_without_filings or request.filings:
                    if request.filings:
                        symbols_with_filings += 1
                        filings_analyzed += len(request.filings)
                    await self._runtime.ingest_analysis(self._engine.evaluate(request))
                    analyses_published += 1
            except Exception as error:
                failures += 1
                if self._on_error is None:
                    raise
                self._on_error(symbol, error)
            if self._spacing and index + 1 < len(normalized):
                await asyncio.sleep(self._spacing)
        return SecRefreshSummary(
            symbols_scanned=len(normalized),
            symbols_with_filings=symbols_with_filings,
            filings_analyzed=filings_analyzed,
            analyses_published=analyses_published,
            failures=failures,
        )
