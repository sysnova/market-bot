"""Bounded one-shot orchestration for a complete symbol analysis."""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol, cast

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

    async def analyze(self, symbol: str, *, timeout_seconds: float) -> dict[str, object]:
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
    mirror_to_nats: bool = False,
) -> dict[str, object]:
    """Run engines in a private process and receive JSON directly.

    mirror_to_nats is retained for CLI/API compatibility; manual runs never use NATS.
    """
    normalized = symbol.strip().upper()
    if not _SYMBOL.fullmatch(normalized):
        raise ValueError("a valid market symbol is required")
    if timeout_seconds <= 0:
        raise ValueError("analysis timeout must be positive")
    # Windows CLI uses SelectorEventLoop for PostgreSQL; it cannot spawn async children.
    # Keep the process handle here so cancellation always terminates the worker.
    process = subprocess.Popen(  # noqa: ASYNC220, S603 -- validated symbol, fixed executable, no shell
        [
            sys.executable,
            "-B",
            "-m",
            "app.integration.manual_analysis",
            normalized,
            str(timeout_seconds),
            str(runtime_root),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    try:
        async with asyncio.timeout(timeout_seconds * 3 + 30):
            stdout, _stderr = await asyncio.to_thread(process.communicate)
        if process.returncode != 0:
            # Do not expose provider credentials or unbounded diagnostic output.
            raise RuntimeError(f"manual analysis process exited with code {process.returncode}")
        report = json.loads(stdout)
        if not isinstance(report, dict) or report.get("symbol") != normalized:
            raise RuntimeError("manual analysis returned an invalid report")
        return cast("dict[str, object]", report)
    finally:
        if process.returncode is None:
            await asyncio.to_thread(_terminate_process, process)
            await asyncio.to_thread(process.wait)


def _terminate_process(process: subprocess.Popen[bytes]) -> None:
    if sys.platform == "win32":
        # The Windows venv launcher creates a Python child; terminate the whole request.
        subprocess.run(  # noqa: S603 -- only the PID of our own child process
            [
                str(Path(os.environ["SYSTEMROOT"]) / "System32" / "taskkill.exe"),
                "/PID",
                str(process.pid),
                "/T",
                "/F",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
            check=False,
        )
    else:
        process.kill()
