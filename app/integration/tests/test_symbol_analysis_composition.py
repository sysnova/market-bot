import asyncio
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from app.integration.symbol_analysis_composition import (
    AnalysisSkipped,
    AnalysisStep,
    SymbolAnalysisOrchestrator,
    run_market_analyzer,
)

NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)


class FixedClock:
    def now(self) -> datetime:
        return NOW


@pytest.mark.unit
async def test_analyze_runs_core_then_parallel_engines_then_fusion() -> None:
    calls: list[str] = []
    peers_done = asyncio.Event()
    peer_count = 0

    async def core(symbol: str) -> dict[str, object]:
        calls.append(f"core:{symbol}")
        return {"analyses": 3}

    async def peer(name: str, symbol: str) -> dict[str, object]:
        nonlocal peer_count
        assert calls[0] == "core:TEST"
        calls.append(f"{name}:{symbol}")
        peer_count += 1
        if peer_count == 2:
            peers_done.set()
        return {"service": name}

    async def fusion(symbol: str) -> dict[str, object]:
        assert peers_done.is_set()
        calls.append(f"fusion:{symbol}")
        return {"state": "OBSERVING"}

    orchestrator = SymbolAnalysisOrchestrator(
        core=AnalysisStep("core", core),
        parallel=(
            AnalysisStep("sec", lambda symbol: peer("sec", symbol)),
            AnalysisStep("support-confirmation", lambda symbol: peer("support", symbol)),
        ),
        fusion=AnalysisStep("signal-fusion", fusion),
        clock=FixedClock(),
    )

    report = await orchestrator.analyze(" test ", timeout_seconds=1)

    assert report["symbol"] == "TEST"
    assert report["generated_at"] == NOW.isoformat()
    assert report["execution_enabled"] is False
    assert report["excluded_engines"] == {
        "peter-lynch": "excluded_by_design_slow_provider",
        "dilution-sec": "excluded_by_design_slow_provider",
    }
    assert [item["engine"] for item in report["engines"]] == [
        "core",
        "sec",
        "support-confirmation",
        "signal-fusion",
    ]
    assert all(item["status"] == "COMPLETED" for item in report["engines"])
    assert calls[0] == "core:TEST"
    assert calls[-1] == "fusion:TEST"
    assert all("peter" not in item for item in calls)


@pytest.mark.unit
async def test_analyze_isolates_timeout_and_failure_without_skipping_fusion() -> None:
    async def complete(_symbol: str) -> dict[str, object]:
        return {"ok": True}

    async def slow(_symbol: str) -> dict[str, object]:
        await asyncio.Event().wait()
        return {}

    async def fail(_symbol: str) -> dict[str, object]:
        raise RuntimeError("provider unavailable")

    async def skip(_symbol: str) -> dict[str, object]:
        raise AnalysisSkipped("positive_holding_required")

    orchestrator = SymbolAnalysisOrchestrator(
        core=AnalysisStep("core", complete),
        parallel=(
            AnalysisStep("slow", slow),
            AnalysisStep("failed", fail),
            AnalysisStep("skipped", skip),
        ),
        fusion=AnalysisStep("signal-fusion", complete),
        clock=FixedClock(),
    )

    report = await orchestrator.analyze("TEST", timeout_seconds=0.01)
    by_engine = {item["engine"]: item for item in report["engines"]}

    assert by_engine["slow"] == {
        "engine": "slow",
        "status": "TIMED_OUT",
        "error_type": "TimeoutError",
    }
    assert by_engine["failed"] == {
        "engine": "failed",
        "status": "FAILED",
        "error_type": "RuntimeError",
        "error": "provider unavailable",
    }
    assert by_engine["signal-fusion"]["status"] == "COMPLETED"
    assert by_engine["skipped"] == {
        "engine": "skipped",
        "status": "SKIPPED",
        "reason": "positive_holding_required",
    }
    assert report["completed"] == 2
    assert report["degraded"] == 2
    assert report["skipped"] == 1


@pytest.mark.unit
async def test_analyze_rejects_unsafe_symbol() -> None:
    async def complete(_symbol: str) -> dict[str, object]:
        return {}

    orchestrator = SymbolAnalysisOrchestrator(
        core=AnalysisStep("core", complete),
        clock=FixedClock(),
    )

    with pytest.raises(ValueError, match="valid market symbol"):
        await orchestrator.analyze("TEST;DROP", timeout_seconds=1)

    with pytest.raises(ValueError, match="positive"):
        await orchestrator.analyze("TEST", timeout_seconds=0)


@pytest.mark.unit
async def test_manual_analyzer_uses_child_stdout_even_when_legacy_nats_flag_is_true() -> None:
    process = Mock()
    process.returncode = 0
    process.communicate.return_value = (b'{"symbol":"ABCD","transport":"DIRECT_ISOLATED"}', b"")
    with patch("subprocess.Popen", return_value=process) as spawn:
        report = await run_market_analyzer(
            symbol="abcd",
            timeout_seconds=1,
            runtime_root=Path(".runtime"),
            mirror_to_nats=True,
        )
    assert report["transport"] == "DIRECT_ISOLATED"
    args = spawn.call_args.args[0]
    assert "app.integration.manual_analysis" in args
    assert "ABCD" in args
    assert "--shared-cache" not in args
    assert "--nats" not in args


@pytest.mark.unit
async def test_cancel_manual_analysis_terminates_child() -> None:
    import threading

    started, released = threading.Event(), threading.Event()
    process = Mock()
    process.returncode = None

    def communicate() -> tuple[bytes, bytes]:
        started.set()
        released.wait(5)
        return b"", b""

    process.communicate.side_effect = communicate
    process.kill.side_effect = released.set
    with (
        patch("subprocess.Popen", return_value=process),
        patch(
            "app.integration.symbol_analysis_composition._terminate_process",
            side_effect=lambda child: child.kill(),
        ) as terminate,
    ):
        task = asyncio.create_task(
            run_market_analyzer(
                symbol="ASTS",
                timeout_seconds=1,
                runtime_root=Path(".runtime"),
            )
        )
        await asyncio.to_thread(started.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    process.kill.assert_called_once()
    process.wait.assert_called_once()
    terminate.assert_called_once_with(process)
