# pyright: reportPrivateUsage=false

from pathlib import Path

import pytest

import scripts.generate_three_engine_day_path as generator
from scripts.generate_three_engine_day_path import (
    DAILY_SESSIONS,
    _AssessmentBundle,
    _build_template,
    _divergence_indicator,
    _extract_swing_from_analyzer,
    _gamma_indicator,
    _resolve_assessments,
)


def _analyzer_report(symbol: str = "SUZ") -> dict[str, object]:
    return {
        "symbol": symbol,
        "engines": [
            {
                "engine": "core",
                "status": "COMPLETED",
                "result": {
                    "analyses": [
                        {
                            "symbol": symbol,
                            "horizon": "SWING",
                            "verdict": "WATCH",
                            "metrics": [],
                        }
                    ]
                },
            }
        ],
    }


def test_extracts_swing_from_online_analyzer_core_result() -> None:
    swing = _extract_swing_from_analyzer(_analyzer_report("SUZ"), "suz")

    assert swing is not None
    assert swing["symbol"] == "SUZ"
    assert swing["horizon"] == "SWING"


@pytest.mark.asyncio
async def test_missing_cached_swing_runs_online_analyzer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetches = 0

    async def fake_fetch(_settings: object, _symbol: str) -> _AssessmentBundle:
        nonlocal fetches
        fetches += 1
        return _AssessmentBundle()

    async def fake_analyzer(symbol: str, runtime_root: Path) -> dict[str, object]:
        assert symbol == "SUZ"
        assert runtime_root == Path(".runtime")
        return _analyzer_report(symbol)

    monkeypatch.setattr(generator, "_fetch_assessments", fake_fetch)
    monkeypatch.setattr(generator, "_run_online_analyzer", fake_analyzer)

    assessments = await _resolve_assessments(
        object(),  # type: ignore[arg-type]
        "SUZ",
        Path(".runtime"),
    )

    assert fetches == 2
    assert assessments.swing is not None
    assert assessments.swing["symbol"] == "SUZ"


def test_divergence_indicator_preserves_engine_evidence() -> None:
    payload: dict[str, object] = {
        "verdict": "FAVORABLE",
        "direction": "BULLISH",
        "score": "84.5",
        "as_of": "2026-08-16T20:00:00Z",
        "metrics": [
            {"name": "divergence_state", "value": "RECLAIM_CONFIRMED"},
            {"name": "price_pivot_1", "value": "104.20"},
            {"name": "price_pivot_2", "value": "99.10"},
            {"name": "price_pivot_1_at", "value": "2026-06-12T20:00:00Z"},
            {"name": "price_pivot_2_at", "value": "2026-07-24T20:00:00Z"},
            {"name": "price_change_percent", "value": "-4.8944"},
            {"name": "obv_improvement_normalized", "value": "1.75"},
            {"name": "pivot_separation_weeks", "value": 6},
            {"name": "reclaim_trigger", "value": "102.50"},
            {"name": "invalidation", "value": "96.80"},
        ],
    }

    indicator = _divergence_indicator(payload)

    assert indicator["availability"] == "AVAILABLE"
    assert indicator["state"] == "RECLAIM_CONFIRMED"
    assert indicator["score"] == 84.5
    assert indicator["price_pivot_1"] == 104.2
    assert indicator["price_pivot_2_at"] == "2026-07-24T20:00:00Z"
    assert indicator["obv_improvement_normalized"] == 1.75
    assert indicator["reclaim_trigger"] == 102.5
    assert indicator["invalidation"] == 96.8


def test_gamma_indicator_marks_fresh_payload_and_preserves_levels() -> None:
    payload: dict[str, object] = {
        "generated_at": "2026-08-17T15:00:00Z",
        "expires_at": "2026-08-17T21:00:00Z",
        "status": "AVAILABLE",
        "quality_score": "91.2",
        "coverage_ratio": "0.875",
        "gamma_regime": "POSITIVE",
        "directional_bias": "NEUTRAL",
        "net_gamma_ratio": "0.42",
        "call_wall": "110",
        "put_wall": "95",
        "absolute_gamma_wall": "105",
        "max_pain": "102.5",
        "gamma_flip": "98.75",
        "expected_move_low": "96",
        "expected_move_high": "108",
        "pin_risk": True,
        "acceleration_risk": False,
        "dealer_sign_assumption": "CALL_POSITIVE_PUT_NEGATIVE",
        "warnings": ["open_interest_daily_cadence"],
    }

    indicator = _gamma_indicator(payload, now_iso="2026-08-17T18:00:00Z")

    assert indicator["availability"] == "AVAILABLE"
    assert indicator["freshness"] == "VIGENTE"
    assert indicator["quality_score"] == 91.2
    assert indicator["coverage_ratio"] == 0.875
    assert indicator["call_wall"] == 110.0
    assert indicator["gamma_flip"] == 98.75
    assert indicator["pin_risk"] is True
    assert indicator["warnings"] == ["open_interest_daily_cadence"]


def test_missing_and_expired_contexts_are_explicit() -> None:
    assert _divergence_indicator(None)["state"] == "SIN_DATO"
    expired_payload: dict[str, object] = {
        "generated_at": "2026-08-16T15:00:00Z",
        "expires_at": "2026-08-16T21:00:00Z",
        "status": "DEGRADED",
    }

    indicator = _gamma_indicator(
        expired_payload, now_iso="2026-08-17T18:00:00Z"
    )

    assert indicator["freshness"] == "VENCIDO"
    assert indicator["status"] == "DEGRADED"


def test_template_contains_divergence_and_gamma_indicators() -> None:
    data: dict[str, object] = {
        "divergence": {"availability": "SIN_DATO", "state": "SIN_DATO"},
        "gamma": {"availability": "SIN_DATO", "status": "SIN_DATO"},
    }

    template = _build_template("TEST", data)

    assert DAILY_SESSIONS == 21
    assert "últimas 21 ruedas (~1 mes)" in template
    assert "estructura diaria de 21 ruedas" in template
    assert "Divergencia semanal precio/OBV" in template
    assert "Gamma de opciones" in template
    assert "CALL_POSITIVE_PUT_NEGATIVE" in template
