"""Tests specify the deterministic SEC dilution policy."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.contracts import AnalysisHorizon, AnalysisVerdict, PatternDirection
from app.dilution_sec_engine import (
    CompanyFactsSnapshot,
    DilutionEvaluationInput,
    DilutionOfferingStatus,
    DilutionSecEngine,
    DilutionSignal,
    FilingDocumentEvidence,
    FilingDocumentSnippet,
    RiskSeverity,
    SecFiling,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> DilutionEvaluationInput:
    payload = (FIXTURES / name).read_text(encoding="utf-8")
    return DilutionEvaluationInput.model_validate_json(payload)


def test_no_sec_data_is_unknown_instead_of_safe() -> None:
    request = DilutionEvaluationInput(symbol="NONE", as_of=date(2026, 7, 26))

    result = DilutionSecEngine().assess(request)

    assert result.score == 0
    assert result.severity is RiskSeverity.UNKNOWN
    assert result.reasons == ("SEC dilution data unavailable",)
    assert result.evidence == ()


def test_recent_atm_prospectus_is_critical_and_traceable() -> None:
    result = DilutionSecEngine().assess(_fixture("recent_atm.json"))

    assert result.score == 92
    assert result.severity is RiskSeverity.CRITICAL
    assert [item.code for item in result.evidence] == [
        "recent_offering_filing",
        "at_the_market",
        "public_offering",
        "warrants",
    ]
    assert {item.accession_number for item in result.evidence} == {
        "0000000000-26-000101"
    }
    assert result.analyzed_filing_count == 1
    assert result.ignored_stale_filing_count == 0


def test_company_facts_capture_share_growth_and_short_cash_runway() -> None:
    result = DilutionSecEngine().assess(_fixture("financing_pressure.json"))

    assert result.score == 50
    assert result.severity is RiskSeverity.HIGH
    assert [item.code for item in result.evidence] == [
        "shares_outstanding_growth_25pct",
        "cash_runway_under_2_quarters",
    ]
    assert result.metrics.share_growth_percent == pytest.approx(30.0)
    assert result.metrics.cash_runway_quarters == pytest.approx(1.5)


def test_stale_filing_does_not_change_current_risk() -> None:
    request = DilutionEvaluationInput(
        symbol="OLD",
        as_of=date(2026, 7, 26),
        filings=(
            SecFiling(
                accession_number="0000000000-24-000001",
                form="424B5",
                filed_at=date(2024, 1, 1),
                signals=(DilutionSignal.AT_THE_MARKET,),
            ),
        ),
    )

    result = DilutionSecEngine().assess(request)

    assert result.score == 0
    assert result.severity is RiskSeverity.LOW
    assert result.analyzed_filing_count == 0
    assert result.ignored_stale_filing_count == 1


def test_repeated_signals_and_forms_are_scored_only_once() -> None:
    later = SecFiling(
        accession_number="0000000000-26-000002",
        form="424B5",
        filed_at=date(2026, 7, 20),
        signals=(DilutionSignal.WARRANTS,),
    )
    earlier = SecFiling(
        accession_number="0000000000-26-000001",
        form="424B5",
        filed_at=date(2026, 7, 1),
        signals=(DilutionSignal.WARRANTS,),
    )
    engine = DilutionSecEngine()

    forward = engine.assess(
        DilutionEvaluationInput(
            symbol="DUPE", as_of=date(2026, 7, 26), filings=(earlier, later)
        )
    )
    reverse = engine.assess(
        DilutionEvaluationInput(
            symbol="DUPE", as_of=date(2026, 7, 26), filings=(later, earlier)
        )
    )

    assert forward == reverse
    assert engine.evaluate(
        DilutionEvaluationInput(
            symbol="DUPE", as_of=date(2026, 7, 26), filings=(earlier, later)
        )
    ) == engine.evaluate(
        DilutionEvaluationInput(
            symbol="DUPE", as_of=date(2026, 7, 26), filings=(later, earlier)
        )
    )
    assert forward.score == 50
    assert [item.code for item in forward.evidence] == [
        "recent_offering_filing",
        "warrants",
    ]
    assert all(item.accession_number == later.accession_number for item in forward.evidence)


def test_conflicting_duplicate_accessions_are_rejected() -> None:
    first = SecFiling(
        accession_number="0000000000-26-000001",
        form="S-3",
        filed_at=date(2026, 7, 1),
    )
    conflict = first.model_copy(update={"form": "424B5"})
    request = DilutionEvaluationInput(
        symbol="DUPE", as_of=date(2026, 7, 26), filings=(first, conflict)
    )

    with pytest.raises(ValueError, match="conflicting duplicate accession"):
        DilutionSecEngine().assess(request)


def test_future_filings_and_incoherent_fact_pairs_are_rejected() -> None:
    request = DilutionEvaluationInput(
        symbol="TIME",
        as_of=date(2026, 7, 26),
        filings=(
            SecFiling(
                accession_number="0000000000-26-000001",
                form="8-K",
                filed_at=date(2026, 7, 27),
            ),
        ),
    )

    with pytest.raises(ValueError, match="after evaluation date"):
        DilutionSecEngine().assess(request)

    with pytest.raises(ValidationError):
        CompanyFactsSnapshot(
            period_end=date(2026, 6, 30),
            current_shares_outstanding="100",
        )


def test_evaluate_emits_the_shared_dilution_analysis_contract() -> None:
    request = _fixture("recent_atm.json")
    engine = DilutionSecEngine()

    result = engine.evaluate(request)

    assert result == engine.evaluate(request)
    assert result.engine_id == "dilution_sec_engine"
    assert result.horizon is AnalysisHorizon.DILUTION
    assert result.verdict is AnalysisVerdict.AVOID
    assert result.direction is PatternDirection.BEARISH
    assert result.score == 92
    assert result.analysis_id.version == 7
    evidence = next(metric.value for metric in result.metrics if metric.name == "evidence")
    assert len(evidence) == 4


def test_evaluate_exposes_primary_document_evidence() -> None:
    document = FilingDocumentEvidence(
        source_url="https://www.sec.gov/Archives/edgar/data/1/2/primary.htm",
        offering_status=DilutionOfferingStatus.PRICED,
        signals=(DilutionSignal.PUBLIC_OFFERING,),
        amounts=("US$15,635,404.00",),
        share_quantities=("1,028,645 common shares",),
        snippets=(
            FilingDocumentSnippet(
                signal=DilutionSignal.PUBLIC_OFFERING,
                text="We are offering common shares at an offering price of US$15.20.",
            ),
        ),
    )
    request = DilutionEvaluationInput(
        symbol="DOC",
        as_of=date(2026, 7, 26),
        filings=(
            SecFiling(
                accession_number="0000000000-26-000001",
                form="424B5",
                filed_at=date(2026, 7, 20),
                signals=document.signals,
                document_evidence=document,
            ),
        ),
    )

    result = DilutionSecEngine().evaluate(request)
    evidence = next(
        metric.value for metric in result.metrics if metric.name == "document_evidence"
    )

    assert evidence[0]["evidence"]["offering_status"] == "priced"
    assert evidence[0]["evidence"]["share_quantities"] == [
        "1,028,645 common shares"
    ]
