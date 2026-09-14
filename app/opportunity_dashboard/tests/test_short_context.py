from datetime import UTC, datetime

import pytest

from app.opportunity_dashboard.ticker_watch import TickerEvidenceBook

NOW = datetime(2026, 9, 14, 19, 47, 33, tzinfo=UTC)


def asts_book() -> TickerEvidenceBook:
    book = TickerEvidenceBook("ASTS", engine_versions={"alert": "3.10.0"})
    for engine, version, at, metrics, extra in (
        (
            "swing",
            "15.0.0",
            "2026-09-14T19:30:00+00:00",
            {"short_thesis_broken": True, "short_structure_gate_passed": True},
            {"direction": "BEARISH", "verdict": "AVOID"},
        ),
        (
            "intraday",
            "8.0.0",
            "2026-09-14T19:46:00+00:00",
            {"setup": "no_trigger", "short_mature_confirmation_gate_passed": False},
            {"direction": "NEUTRAL", "verdict": "WATCH"},
        ),
    ):
        book.merge(
            "analysis.result.produced",
            {
                "symbol": "ASTS",
                "engine_id": engine,
                "engine_version": version,
                "as_of": at,
                "metrics": [{"name": k, "value": v} for k, v in metrics.items()],
                **extra,
            },
            received_at=NOW,
        )
    book.merge(
        "4hgeri.assessed",
        {"symbol": "ASTS", "occurred_at": NOW.isoformat(), "short_eligible": False},
        received_at=NOW,
    )
    return book


def test_asts_snapshot_does_not_invert_short_structure_or_invent_geri_veto() -> None:
    data = asts_book().snapshot(now=NOW)
    swing = next(c for c in data["assessments"] if c["engine"] == "swing")
    gates = {g["name"]: g for g in swing["gates"]}
    assert gates["short_thesis_broken"]["status"] == "PASS"
    assert gates["short_thesis_broken"]["thesis_scope"] == "SHORT"
    assert "LONG" in gates["short_thesis_broken"]["label"]
    assert gates["short_structure_gate_passed"]["status"] == "PASS"
    context = data["short_context"]
    assert context["route"]["decision_owner"] == "alert"
    assert context["route"]["required_analysis_engines"] == ["swing", "intraday"]
    assert "4hgeri" in context["route"]["not_confirmation_gates"]
    assert context["structure"]["fields"]["short_structure_gate_passed"] is True
    assert context["timing"]["fields"]["short_mature_confirmation_gate_passed"] is False
    assert context["confirmation"] is None
    assert context["full_session_history"] is False


@pytest.mark.parametrize("freshness", ["STALE", "UNKNOWN"])
def test_short_semantics_preserve_unusable_evidence(freshness: str) -> None:
    from app.opportunity_dashboard.ticker_watch import project_gates

    gates = project_gates(
        {
            "engine_id": "swing",
            "engine_version": "15.0.0",
            "metrics": [
                {"name": "short_thesis_broken", "value": True},
                {"name": "structure_broken_confirmed", "value": True},
            ],
        },
        freshness=freshness,
    )
    assert all(g["status"] == freshness for g in gates)


def test_short_semantics_do_not_change_unrelated_or_unknown_engine_versions() -> None:
    from app.opportunity_dashboard.ticker_watch import project_gates

    for engine, version in [("swing", "99.0.0"), ("intraday", "15.0.0")]:
        gates = project_gates(
            {
                "engine_id": engine,
                "engine_version": version,
                "metrics": [
                    {"name": "short_thesis_broken", "value": True},
                    {"name": "structure_broken_confirmed", "value": True},
                ],
            },
            freshness="FRESH",
        )
        assert all(g["status"] == "FAIL" for g in gates)
    book = TickerEvidenceBook("ASTS", engine_versions={"alert": "99.0.0"})
    assert book.snapshot(now=NOW)["short_context"]["route"] is None


def test_only_a_published_short_alert_proves_confirmation_and_keeps_its_age() -> None:
    book = asts_book()
    book.merge(
        "alert.local.produced",
        {
            "symbol": "ASTS",
            "kind": "BEARISH_CONSENSUS",
            "created_at": "2026-09-10T15:00:00+00:00",
            "reasons": ["short_entry_confirmed"],
            "metrics": [{"name": "short_entry_price", "value": "63.25"}],
        },
        received_at=NOW,
    )
    confirmation = book.snapshot(now=NOW)["short_context"]["confirmation"]
    assert confirmation["freshness"] == "STALE"
    assert confirmation["fields"]["short_entry_price"] == "63.25"
    book.merge(
        "alert.local.produced",
        {
            "symbol": "ASTS",
            "kind": "BEARISH_CONSENSUS",
            "created_at": NOW.isoformat(),
            "reasons": ["generic_bearish_consensus"],
        },
        received_at=NOW,
    )
    assert book.snapshot(now=NOW)["short_context"]["confirmation"] is None
