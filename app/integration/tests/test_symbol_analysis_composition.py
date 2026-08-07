import asyncio
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

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
async def test_production_analyzer_propagates_ticker_and_excludes_slow_engines() -> None:
    received: list[tuple[str, object]] = []

    async def live(**kwargs: object) -> dict[str, object]:
        received.append(("core", kwargs["symbols"]))
        return {"symbols": list(kwargs["symbols"]), "analyses": []}  # type: ignore[arg-type]

    async def rotation(**_kwargs: object) -> dict[str, object]:
        return {"service": "rotation"}

    async def long_portfolio(**kwargs: object) -> dict[str, object]:
        received.append(("long-portfolio", kwargs["symbol"]))
        return {"service": "long-portfolio", "eligible": True}

    async def patreon(**kwargs: object) -> dict[str, object]:
        received.append(("patreon-caps", kwargs["symbols"]))
        return {"service": "patreon-caps", "eligible": True}

    async def held_engine(name: str, **kwargs: object) -> dict[str, object]:
        received.append((name, kwargs["symbol"]))
        return {"service": name, "eligible": True}

    with (
        patch("app.integration.live_composition.run_live_analysis", new=live),
        patch(
            "app.integration.market_rotation_composition.run_market_rotation_process",
            new=rotation,
        ),
        patch(
            "app.integration.long_portfolio_composition.run_long_portfolio_process",
            new=long_portfolio,
        ),
        patch(
            "app.integration.patreon_caps_composition.run_patreon_caps_process",
            new=patreon,
        ),
        patch(
            "app.integration.elliott_wave_composition.run_elliott_wave_process",
            new=lambda **kwargs: held_engine("elliott-wave", **kwargs),
        ),
        patch(
            "app.integration.support_confirmation_composition.run_support_confirmation_process",
            new=lambda **kwargs: held_engine("support-confirmation", **kwargs),
        ),
        patch(
            "app.integration.signal_fusion_composition.run_signal_fusion_process",
            new=lambda **kwargs: held_engine("signal-fusion", **kwargs),
        ),
    ):
        report = await run_market_analyzer(
            symbol="abcd",
            timeout_seconds=1,
            runtime_root=Path(".runtime"),
            mirror_to_nats=True,
        )

    assert received == [
        ("core", ("ABCD",)),
        ("long-portfolio", "ABCD"),
        ("patreon-caps", ("ABCD",)),
        ("elliott-wave", "ABCD"),
        ("support-confirmation", "ABCD"),
        ("signal-fusion", "ABCD"),
    ]
    assert report["excluded_engines"] == {
        "peter-lynch": "excluded_by_design_slow_provider",
        "dilution-sec": "excluded_by_design_slow_provider",
    }
    assert all(
        item["engine"] not in {"peter-lynch", "dilution-sec"}
        for item in report["engines"]
    )
