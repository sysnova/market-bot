"""Pure deterministic policy for estimating SEC dilution risk."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from app.common.canonical import sha256_digest
from app.contracts import (
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    NamedValue,
    PatternDirection,
)

from .models import (
    CompanyFactsSnapshot,
    DilutionAssessment,
    DilutionEvaluationInput,
    DilutionMetrics,
    DilutionSignal,
    EvidenceSource,
    RiskEvidence,
    RiskSeverity,
    SecFiling,
)

_MAX_FILING_AGE_DAYS = 365
_OFFERING_FORMS = frozenset({"424B3", "424B5", "FWP", "SUPPL"})
_REGISTRATION_FORMS = frozenset({"S-1", "S-1/A", "S-3", "S-3/A"})
_SIGNAL_POINTS: tuple[tuple[DilutionSignal, int], ...] = (
    (DilutionSignal.AT_THE_MARKET, 20),
    (DilutionSignal.SHELF_REGISTRATION, 8),
    (DilutionSignal.PUBLIC_OFFERING, 22),
    (DilutionSignal.REGISTERED_DIRECT, 25),
    (DilutionSignal.PRIVATE_PLACEMENT, 20),
    (DilutionSignal.SELLING_STOCKHOLDERS, 10),
    (DilutionSignal.WARRANTS, 15),
    (DilutionSignal.CONVERTIBLE_DEBT, 20),
    (DilutionSignal.GOING_CONCERN, 40),
    (DilutionSignal.REVERSE_SPLIT, 20),
)
_SIGNAL_DESCRIPTIONS: dict[DilutionSignal, str] = {
    DilutionSignal.AT_THE_MARKET: "at-the-market issuance language detected",
    DilutionSignal.SHELF_REGISTRATION: "shelf registration capacity detected",
    DilutionSignal.PUBLIC_OFFERING: "public offering language detected",
    DilutionSignal.REGISTERED_DIRECT: "registered-direct financing detected",
    DilutionSignal.PRIVATE_PLACEMENT: "private-placement financing detected",
    DilutionSignal.SELLING_STOCKHOLDERS: "selling-stockholder overhang detected",
    DilutionSignal.WARRANTS: "warrant issuance or exercise exposure detected",
    DilutionSignal.CONVERTIBLE_DEBT: "convertible-debt exposure detected",
    DilutionSignal.GOING_CONCERN: "going-concern uncertainty detected",
    DilutionSignal.REVERSE_SPLIT: "reverse-split signal detected",
}


class DilutionSecEngine:
    """Score normalized SEC inputs without I/O, clocks, or cross-engine imports."""

    ENGINE_ID = "dilution_sec_engine"
    ENGINE_VERSION = "1.0.0"

    def evaluate(self, request: DilutionEvaluationInput) -> AnalysisResult:
        """Emit the stable cross-engine analysis contract for this SEC snapshot."""

        assessment = self.assess(request)
        context_hash = self._context_hash(request)
        metrics: list[NamedValue] = [
            NamedValue(name="dilution_severity", value=assessment.severity.value),
            NamedValue(
                name="evidence",
                value=[item.model_dump(mode="json") for item in assessment.evidence],
            ),
            NamedValue(
                name="analyzed_filing_count",
                value=assessment.analyzed_filing_count,
            ),
            NamedValue(
                name="ignored_stale_filing_count",
                value=assessment.ignored_stale_filing_count,
            ),
        ]
        if assessment.metrics.share_growth_percent is not None:
            metrics.append(
                NamedValue(
                    name="share_growth_percent",
                    value=assessment.metrics.share_growth_percent,
                )
            )
        if assessment.metrics.cash_runway_quarters is not None:
            metrics.append(
                NamedValue(
                    name="cash_runway_quarters",
                    value=assessment.metrics.cash_runway_quarters,
                )
            )
        document_evidence = [
            {
                "accession_number": filing.accession_number,
                "form": filing.form,
                "evidence": (
                    filing.document_evidence.model_dump(mode="json")
                    if filing.document_evidence is not None
                    else None
                ),
                "error": filing.document_error,
            }
            for filing in request.filings
            if filing.document_evidence is not None or filing.document_error is not None
        ]
        if document_evidence:
            metrics.append(NamedValue(name="document_evidence", value=document_evidence))
        return AnalysisResult(
            analysis_id=self._analysis_id(request, context_hash),
            engine_id=self.ENGINE_ID,
            engine_version=self.ENGINE_VERSION,
            symbol=assessment.symbol,
            horizon=AnalysisHorizon.DILUTION,
            as_of=datetime.combine(assessment.as_of, datetime.min.time(), tzinfo=UTC),
            verdict=self._verdict(assessment.severity),
            direction=(
                PatternDirection.BEARISH
                if assessment.severity
                in {RiskSeverity.MEDIUM, RiskSeverity.HIGH, RiskSeverity.CRITICAL}
                else PatternDirection.NEUTRAL
            ),
            score=Decimal(assessment.score),
            confidence=self._confidence(request),
            reasons=assessment.reasons,
            metrics=tuple(metrics),
            context_hash=context_hash,
        )

    def assess(self, request: DilutionEvaluationInput) -> DilutionAssessment:
        """Return detailed evidence for the same input, independent of input order."""

        filings = self._deduplicate_filings(request.filings)
        self._validate_dates(request.as_of, filings, request.facts)
        if not filings and request.facts is None:
            return self._unknown(request)

        current = tuple(
            filing
            for filing in filings
            if (request.as_of - filing.filed_at).days <= _MAX_FILING_AGE_DAYS
        )
        stale_count = len(filings) - len(current)
        evidence = [*self._filing_evidence(request.as_of, current)]
        metrics, fact_evidence = self._fact_evidence(request.facts)
        evidence.extend(fact_evidence)
        score = min(100, sum(item.points for item in evidence))
        reasons = tuple(item.description for item in evidence)
        if not reasons:
            reasons = ("No current SEC dilution risk signals detected",)
        return DilutionAssessment(
            symbol=request.symbol,
            as_of=request.as_of,
            score=score,
            severity=self._severity(score),
            evidence=tuple(evidence),
            reasons=reasons,
            metrics=metrics,
            analyzed_filing_count=len(current),
            ignored_stale_filing_count=stale_count,
        )

    @staticmethod
    def _deduplicate_filings(filings: tuple[SecFiling, ...]) -> tuple[SecFiling, ...]:
        unique: dict[str, SecFiling] = {}
        for filing in filings:
            existing = unique.get(filing.accession_number)
            if existing is not None and existing != filing:
                raise ValueError(
                    f"conflicting duplicate accession: {filing.accession_number}"
                )
            unique[filing.accession_number] = filing
        return tuple(
            sorted(
                unique.values(),
                key=lambda item: (item.filed_at, item.accession_number),
                reverse=True,
            )
        )

    @staticmethod
    def _validate_dates(
        as_of: date,
        filings: tuple[SecFiling, ...],
        facts: CompanyFactsSnapshot | None,
    ) -> None:
        future = next((item for item in filings if item.filed_at > as_of), None)
        if future is not None:
            raise ValueError(
                f"filing {future.accession_number} is after evaluation date {as_of}"
            )
        if facts is not None and facts.period_end > as_of:
            raise ValueError("CompanyFacts period end is after evaluation date")

    def _filing_evidence(
        self,
        as_of: date,
        filings: tuple[SecFiling, ...],
    ) -> tuple[RiskEvidence, ...]:
        evidence: list[RiskEvidence] = []
        form_candidates = tuple(
            (self._form_points(as_of, filing), filing)
            for filing in filings
            if self._form_points(as_of, filing) > 0
        )
        if form_candidates:
            points, filing = max(
                form_candidates,
                key=lambda item: (item[0], item[1].filed_at, item[1].accession_number),
            )
            code = (
                "recent_offering_filing"
                if filing.form.upper() in _OFFERING_FORMS
                else "recent_registration_filing"
            )
            evidence.append(
                RiskEvidence(
                    code=code,
                    source=EvidenceSource.FILING,
                    points=points,
                    description=(
                        f"SEC {filing.form.upper()} filed {filing.filed_at.isoformat()} "
                        "indicates recent financing capacity or activity"
                    ),
                    observed_at=filing.filed_at,
                    accession_number=filing.accession_number,
                )
            )

        for signal, points in _SIGNAL_POINTS:
            matches = tuple(filing for filing in filings if signal in filing.signals)
            if not matches:
                continue
            filing = max(matches, key=lambda item: (item.filed_at, item.accession_number))
            evidence.append(
                RiskEvidence(
                    code=signal.value,
                    source=EvidenceSource.FILING,
                    points=points,
                    description=_SIGNAL_DESCRIPTIONS[signal],
                    observed_at=filing.filed_at,
                    accession_number=filing.accession_number,
                )
            )
        return tuple(evidence)

    @staticmethod
    def _form_points(as_of: date, filing: SecFiling) -> int:
        age_days = (as_of - filing.filed_at).days
        form = filing.form.upper()
        if form in _OFFERING_FORMS:
            if age_days <= 45:
                return 35
            if age_days <= 180:
                return 25
            return 12
        if form in _REGISTRATION_FORMS:
            if age_days <= 45:
                return 20
            if age_days <= 180:
                return 12
            return 6
        return 0

    @staticmethod
    def _fact_evidence(
        facts: CompanyFactsSnapshot | None,
    ) -> tuple[DilutionMetrics, tuple[RiskEvidence, ...]]:
        if facts is None:
            return DilutionMetrics(), ()
        evidence: list[RiskEvidence] = []
        growth: Decimal | None = None
        runway: Decimal | None = None
        current = facts.current_shares_outstanding
        prior = facts.prior_year_shares_outstanding
        if current is not None and prior is not None:
            growth = ((current - prior) / prior * Decimal("100")).quantize(
                Decimal("0.01")
            )
            if growth >= Decimal("25"):
                points, code = 30, "shares_outstanding_growth_25pct"
            elif growth >= Decimal("10"):
                points, code = 20, "shares_outstanding_growth_10pct"
            elif growth >= Decimal("3"):
                points, code = 10, "shares_outstanding_growth_3pct"
            else:
                points, code = 0, ""
            if points:
                evidence.append(
                    RiskEvidence(
                        code=code,
                        source=EvidenceSource.COMPANY_FACT,
                        points=points,
                        description=f"shares outstanding increased {growth}% year over year",
                        observed_at=facts.period_end,
                        source_accessions=tuple(sorted(set(facts.source_accessions))),
                    )
                )

        cash = facts.cash_and_equivalents
        operating_cash_flow = facts.quarterly_operating_cash_flow
        if cash is not None and operating_cash_flow is not None and operating_cash_flow < 0:
            runway = (cash / -operating_cash_flow).quantize(Decimal("0.01"))
            if runway < Decimal("2"):
                points, code = 20, "cash_runway_under_2_quarters"
            elif runway < Decimal("4"):
                points, code = 10, "cash_runway_under_4_quarters"
            else:
                points, code = 0, ""
            if points:
                evidence.append(
                    RiskEvidence(
                        code=code,
                        source=EvidenceSource.COMPANY_FACT,
                        points=points,
                        description=(
                            f"cash covers approximately {runway} quarters at current "
                            "operating cash burn"
                        ),
                        observed_at=facts.period_end,
                        source_accessions=tuple(sorted(set(facts.source_accessions))),
                    )
                )
        return DilutionMetrics(
            share_growth_percent=growth,
            cash_runway_quarters=runway,
        ), tuple(evidence)

    @staticmethod
    def _severity(score: int) -> RiskSeverity:
        if score >= 70:
            return RiskSeverity.CRITICAL
        if score >= 35:
            return RiskSeverity.HIGH
        if score >= 15:
            return RiskSeverity.MEDIUM
        return RiskSeverity.LOW

    @staticmethod
    def _verdict(severity: RiskSeverity) -> AnalysisVerdict:
        return {
            RiskSeverity.UNKNOWN: AnalysisVerdict.INSUFFICIENT_DATA,
            RiskSeverity.LOW: AnalysisVerdict.FAVORABLE,
            RiskSeverity.MEDIUM: AnalysisVerdict.WATCH,
            RiskSeverity.HIGH: AnalysisVerdict.CAUTION,
            RiskSeverity.CRITICAL: AnalysisVerdict.AVOID,
        }[severity]

    @staticmethod
    def _confidence(request: DilutionEvaluationInput) -> Decimal:
        if not request.filings and request.facts is None:
            return Decimal("0")
        if request.filings and request.facts is not None:
            return Decimal("0.90")
        return Decimal("0.70")

    def _context_hash(self, request: DilutionEvaluationInput) -> str:
        filings = self._deduplicate_filings(request.filings)
        payload = {
            "symbol": request.symbol,
            "as_of": request.as_of,
            "filings": [item.model_dump(mode="python") for item in filings],
            "facts": (
                request.facts.model_dump(mode="python")
                if request.facts is not None
                else None
            ),
        }
        return f"sha256:{sha256_digest(payload)}"

    @staticmethod
    def _analysis_id(request: DilutionEvaluationInput, context_hash: str) -> UUID:
        timestamp = datetime.combine(request.as_of, datetime.min.time(), tzinfo=UTC)
        timestamp_ms = int(timestamp.timestamp() * 1_000) & ((1 << 48) - 1)
        random_bits = int(context_hash.removeprefix("sha256:")[:19], 16) & ((1 << 74) - 1)
        value = timestamp_ms << 80
        value |= 0x7 << 76
        value |= ((random_bits >> 62) & 0xFFF) << 64
        value |= 0b10 << 62
        value |= random_bits & ((1 << 62) - 1)
        return UUID(int=value)

    @staticmethod
    def _unknown(request: DilutionEvaluationInput) -> DilutionAssessment:
        return DilutionAssessment(
            symbol=request.symbol,
            as_of=request.as_of,
            score=0,
            severity=RiskSeverity.UNKNOWN,
            evidence=(),
            reasons=("SEC dilution data unavailable",),
            metrics=DilutionMetrics(),
            analyzed_filing_count=0,
            ignored_stale_filing_count=0,
        )
