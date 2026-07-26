"""Scheduled SEC analysis kept outside the realtime market tick path."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
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
    ) -> None:
        if request_spacing_seconds < 0:
            raise ValueError("request_spacing_seconds cannot be negative")
        self._resolver = resolver
        self._loader = loader
        self._engine = engine
        self._runtime = runtime
        self._spacing = request_spacing_seconds
        self._on_error = on_error

    async def refresh(self, symbols: tuple[str, ...], as_of: datetime) -> None:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("SEC refresh time must be timezone-aware")
        normalized = tuple(dict.fromkeys(symbol.strip().upper() for symbol in symbols))
        for index, symbol in enumerate(normalized):
            try:
                cik = await self._resolver.resolve(symbol)
                request = await self._loader.load(
                    cik=cik,
                    symbol=symbol,
                    as_of=as_of.date(),
                )
                await self._runtime.ingest_analysis(self._engine.evaluate(request))
            except Exception as error:
                if self._on_error is None:
                    raise
                self._on_error(symbol, error)
            if self._spacing and index + 1 < len(normalized):
                await asyncio.sleep(self._spacing)
