"""Bounded one-shot orchestration for a complete symbol analysis."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from app.common.clock import SystemClock

AnalysisRunner = Callable[[str], Awaitable[dict[str, object]]]
_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.-]{0,14}$")


class AnalysisSkipped(RuntimeError):
    """Raised when an engine is intentionally outside a one-shot symbol run."""


class AnalysisClock(Protocol):
    def now(self) -> datetime: ...


@dataclass(frozen=True, slots=True)
class AnalysisStep:
    """One independently bounded engine invocation."""

    name: str
    run: AnalysisRunner


class SymbolAnalysisOrchestrator:
    """Run core first, independent peers concurrently, and Fusion last."""

    def __init__(
        self,
        *,
        core: AnalysisStep,
        parallel: tuple[AnalysisStep, ...] = (),
        fusion: AnalysisStep | None = None,
        clock: AnalysisClock | None = None,
    ) -> None:
        names = (core.name, *(item.name for item in parallel))
        if fusion is not None:
            names = (*names, fusion.name)
        if len(names) != len(set(names)):
            raise ValueError("analysis step names must be unique")
        if "peter-lynch" in names:
            raise ValueError("Peter Lynch is excluded from symbol analysis")
        self._core = core
        self._parallel = parallel
        self._fusion = fusion
        self._clock = clock or SystemClock()

    async def analyze(
        self, symbol: str, *, timeout_seconds: float
    ) -> dict[str, object]:
        """Return one stable report even when individual engines fail or time out."""

        normalized = symbol.strip().upper()
        if not _SYMBOL.fullmatch(normalized):
            raise ValueError("a valid market symbol is required")
        if timeout_seconds <= 0:
            raise ValueError("analysis timeout must be positive")

        results = [
            await self._run_step(
                self._core,
                normalized,
                timeout_seconds=timeout_seconds,
            )
        ]
        if self._parallel:
            results.extend(
                await asyncio.gather(
                    *(
                        self._run_step(
                            step,
                            normalized,
                            timeout_seconds=timeout_seconds,
                        )
                        for step in self._parallel
                    )
                )
            )
        if self._fusion is not None:
            results.append(
                await self._run_step(
                    self._fusion,
                    normalized,
                    timeout_seconds=timeout_seconds,
                )
            )
        completed = sum(item["status"] == "COMPLETED" for item in results)
        skipped = sum(item["status"] == "SKIPPED" for item in results)
        return {
            "symbol": normalized,
            "generated_at": self._clock.now().isoformat(),
            "execution_enabled": False,
            "excluded_engines": {
                "peter-lynch": "excluded_by_design_slow_provider",
                "dilution-sec": "excluded_by_design_slow_provider",
            },
            "engines": results,
            "completed": completed,
            "degraded": len(results) - completed - skipped,
            "skipped": skipped,
        }

    @staticmethod
    async def _run_step(
        step: AnalysisStep,
        symbol: str,
        *,
        timeout_seconds: float,
    ) -> dict[str, object]:
        try:
            async with asyncio.timeout(timeout_seconds):
                result = await step.run(symbol)
        except TimeoutError:
            return {
                "engine": step.name,
                "status": "TIMED_OUT",
                "error_type": "TimeoutError",
            }
        except AnalysisSkipped as error:
            return {
                "engine": step.name,
                "status": "SKIPPED",
                "reason": str(error),
            }
        except Exception as error:
            return {
                "engine": step.name,
                "status": "FAILED",
                "error_type": type(error).__name__,
                "error": str(error),
            }
        return {
            "engine": step.name,
            "status": "COMPLETED",
            "result": result,
        }


async def run_market_analyzer(
    *,
    symbol: str,
    timeout_seconds: float,
    runtime_root: Path,
    mirror_to_nats: bool,
) -> dict[str, object]:
    """Build and run the production analyzer without Peter Lynch or SEC."""

    async def core(received_symbol: str) -> dict[str, object]:
        from app.integration.live_composition import run_live_analysis

        result = await run_live_analysis(
            once=True,
            runtime_root=runtime_root,
            bell=False,
            mirror_to_nats=mirror_to_nats,
            symbols=(received_symbol,),
            include_analyses=True,
        )
        if result is None:
            raise RuntimeError("core one-shot analysis returned no summary")
        return {
            **result,
            "orchestrated_engines": [
                "long-term",
                "swing",
                "intraday",
                "entry-watcher",
                "alert-v2",
            ],
        }

    async def rotation(received_symbol: str) -> dict[str, object]:
        _require_nats(mirror_to_nats)
        from app.integration.market_rotation_composition import (
            run_market_rotation_process,
        )

        result = await run_market_rotation_process(once=True, ready_path=None)
        if result is None:
            raise RuntimeError("market rotation returned no summary")
        return {**result, "requested_symbol": received_symbol, "scope": "global-market"}

    async def long_portfolio(received_symbol: str) -> dict[str, object]:
        _require_nats(mirror_to_nats)
        from app.integration.long_portfolio_composition import (
            run_long_portfolio_process,
        )

        result = await run_long_portfolio_process(
            runtime_root=runtime_root,
            ready_path=None,
            once=True,
            symbol=received_symbol,
        )
        return _applicable(result, "long-portfolio returned no summary")

    async def patreon(received_symbol: str) -> dict[str, object]:
        _require_nats(mirror_to_nats)
        from app.integration.patreon_caps_composition import (
            run_patreon_caps_process,
        )

        result = await run_patreon_caps_process(
            ready_path=None,
            once=True,
            symbols=(received_symbol,),
        )
        return _applicable(result, "Patreon Caps returned no summary")

    async def elliott(received_symbol: str) -> dict[str, object]:
        _require_nats(mirror_to_nats)
        from app.integration.elliott_wave_composition import (
            run_elliott_wave_process,
        )

        result = await run_elliott_wave_process(
            ready_path=None,
            once=True,
            symbol=received_symbol,
        )
        return _applicable(result, "Elliott Wave returned no summary")

    async def support(received_symbol: str) -> dict[str, object]:
        _require_nats(mirror_to_nats)
        from app.integration.support_confirmation_composition import (
            run_support_confirmation_process,
        )

        result = await run_support_confirmation_process(
            ready_path=None,
            once=True,
            symbol=received_symbol,
        )
        return _applicable(result, "Support Confirmation returned no summary")

    async def portfolio_flow(_received_symbol: str) -> dict[str, object]:
        raise AnalysisSkipped("requires_live_quote_trade_window")

    async def fusion(received_symbol: str) -> dict[str, object]:
        _require_nats(mirror_to_nats)
        from app.integration.signal_fusion_composition import (
            run_signal_fusion_process,
        )

        result = await run_signal_fusion_process(
            ready_path=None,
            once=True,
            symbol=received_symbol,
        )
        return _applicable(result, "Signal Fusion returned no summary")

    return await SymbolAnalysisOrchestrator(
        core=AnalysisStep("core", core),
        parallel=(
            AnalysisStep("market-rotation", rotation),
            AnalysisStep("long-portfolio", long_portfolio),
            AnalysisStep("patreon-caps", patreon),
            AnalysisStep("elliott-wave", elliott),
            AnalysisStep("support-confirmation", support),
            AnalysisStep("portfolio-flow", portfolio_flow),
        ),
        fusion=AnalysisStep("signal-fusion", fusion),
    ).analyze(symbol, timeout_seconds=timeout_seconds)


def _require_nats(enabled: bool) -> None:
    if not enabled:
        raise AnalysisSkipped("distributed_engine_requires_nats")


def _applicable(
    result: dict[str, object] | None,
    missing_message: str,
) -> dict[str, object]:
    if result is None:
        raise RuntimeError(missing_message)
    if result.get("eligible") is False:
        raise AnalysisSkipped(str(result.get("reason", "symbol_not_eligible")))
    return result
