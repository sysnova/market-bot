from datetime import UTC, datetime, timedelta

import pytest

from app.opportunity_dashboard.ticker_watch import TickerEvidenceBook, normalize_symbol

NOW = datetime(2026, 9, 14, 14, 30, tzinfo=UTC)


def evidence(symbol: str = "NVDA", **updates: object) -> dict[str, object]:
    return {
        "symbol": symbol,
        "engine_id": "swing",
        "horizon": "SWING",
        "as_of": NOW.isoformat(),
        "engine_version": "15.0.0",
        "metrics": [
            {"name": "entry_gate_passed", "value": True},
            {"name": "structure_broken_confirmed", "value": True},
            {"name": "unknown_gate", "value": None},
            {"name": "places_orders", "value": False},
        ],
        **updates,
    }


def test_gates_keep_negative_polarity_unknown_and_complete_evidence() -> None:
    book = TickerEvidenceBook("nvda", engines={"swing": "active", "4hgeri": "active"})
    assert book.merge("analysis.result.produced", evidence(), received_at=NOW)
    result = book.snapshot(now=NOW)
    assessment = result["assessments"][0]
    gates = {item["name"]: item for item in assessment["gates"]}
    assert gates["entry_gate_passed"]["status"] == "PASS"
    assert gates["structure_broken_confirmed"]["status"] == "FAIL"
    assert gates["unknown_gate"]["status"] == "UNKNOWN"
    assert "places_orders" not in gates
    assert assessment["payload"] == evidence()
    assert "4hgeri" in result["missing_engines"]


def test_old_other_symbol_and_expired_evidence_never_turn_green() -> None:
    book = TickerEvidenceBook("NVDA")
    book.merge("analysis.result.produced", evidence(), received_at=NOW)
    assert not book.merge("analysis.result.produced", evidence("AAPL"), received_at=NOW)
    assert not book.merge(
        "analysis.result.produced",
        evidence(as_of=(NOW - timedelta(minutes=5)).isoformat()),
        received_at=NOW,
    )
    stale = book.snapshot(now=NOW + timedelta(minutes=20))["assessments"][0]
    assert stale["freshness"] == "STALE"
    assert all(gate["status"] in {"STALE", "UNKNOWN"} for gate in stale["gates"])


def test_nested_analyses_and_distinct_horizons_are_retained() -> None:
    book = TickerEvidenceBook("NVDA")
    book.merge(
        "entry-setup.assessed",
        {
            "symbol": "NVDA",
            "family": "CORE",
            "assessed_at": NOW.isoformat(),
            "component_analyses": [evidence(), evidence(engine_id="intraday", horizon="INTRADAY")],
        },
        received_at=NOW,
    )
    result = book.snapshot(now=NOW)
    assert {item["engine"] for item in result["assessments"]} == {
        "entry-setup",
        "swing",
        "intraday",
    }


@pytest.mark.parametrize("symbol", ["*", "AAPL.>", "", "<script>", "A B"])
def test_invalid_tickers_are_rejected(symbol: str) -> None:
    with pytest.raises(ValueError):
        normalize_symbol(symbol)
