"""Immutable input and output values for SEC dilution analysis."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import Field, StringConstraints, field_validator, model_validator

from app.contracts._base import StrictFrozenModel

NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Symbol = Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9.-]{0,14}$")]
AccessionNumber = Annotated[
    str,
    StringConstraints(pattern=r"^\d{10}-\d{2}-\d{6}$"),
]


class DilutionSignal(StrEnum):
    """Normalized evidence extracted by an upstream SEC document adapter."""

    AT_THE_MARKET = "at_the_market"
    SHELF_REGISTRATION = "shelf_registration"
    PUBLIC_OFFERING = "public_offering"
    REGISTERED_DIRECT = "registered_direct"
    PRIVATE_PLACEMENT = "private_placement"
    SELLING_STOCKHOLDERS = "selling_stockholders"
    WARRANTS = "warrants"
    CONVERTIBLE_DEBT = "convertible_debt"
    GOING_CONCERN = "going_concern"
    REVERSE_SPLIT = "reverse_split"


class DilutionOfferingStatus(StrEnum):
    """Strongest transaction stage explicitly supported by document text."""

    UNKNOWN = "unknown"
    CAPACITY = "capacity"
    ANNOUNCED = "announced"
    PRICED = "priced"
    COMPLETED = "completed"


class FilingDocumentSnippet(StrictFrozenModel):
    """Short filing excerpt proving one normalized signal."""

    signal: DilutionSignal
    text: NonEmptyText


class FilingDocumentEvidence(StrictFrozenModel):
    """Bounded, auditable evidence extracted from one primary SEC document."""

    source_url: NonEmptyText
    offering_status: DilutionOfferingStatus = DilutionOfferingStatus.UNKNOWN
    signals: tuple[DilutionSignal, ...] = ()
    amounts: tuple[NonEmptyText, ...] = ()
    share_quantities: tuple[NonEmptyText, ...] = ()
    snippets: tuple[FilingDocumentSnippet, ...] = ()
    truncated: bool = False

    @field_validator("signals", mode="after")
    @classmethod
    def normalize_document_signals(
        cls, value: tuple[DilutionSignal, ...]
    ) -> tuple[DilutionSignal, ...]:
        return tuple(sorted(set(value), key=lambda item: item.value))


class RiskSeverity(StrEnum):
    """Operator-facing severity derived exclusively from the numeric score."""

    UNKNOWN = "unknown"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class EvidenceSource(StrEnum):
    FILING = "filing"
    COMPANY_FACT = "company_fact"


class SecFiling(StrictFrozenModel):
    """SEC filing metadata with document signals normalized outside this engine."""

    accession_number: AccessionNumber
    form: NonEmptyText
    filed_at: date
    primary_document_description: str = ""
    signals: tuple[DilutionSignal, ...] = ()
    document_evidence: FilingDocumentEvidence | None = None
    document_error: str | None = None

    @field_validator("form", mode="before")
    @classmethod
    def normalize_form(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().upper()
        return value

    @field_validator("signals", mode="after")
    @classmethod
    def normalize_signals(
        cls, value: tuple[DilutionSignal, ...]
    ) -> tuple[DilutionSignal, ...]:
        return tuple(sorted(set(value), key=lambda item: item.value))


class CompanyFactsSnapshot(StrictFrozenModel):
    """Point-in-time values mapped from SEC CompanyFacts by an upstream adapter."""

    period_end: date
    current_shares_outstanding: Decimal | None = Field(default=None, gt=0)
    prior_year_shares_outstanding: Decimal | None = Field(default=None, gt=0)
    cash_and_equivalents: Decimal | None = Field(default=None, ge=0)
    quarterly_operating_cash_flow: Decimal | None = None
    source_accessions: tuple[AccessionNumber, ...] = ()

    @field_validator("source_accessions", mode="after")
    @classmethod
    def normalize_accessions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted(set(value)))

    @model_validator(mode="after")
    def validate_fact_pairs(self) -> CompanyFactsSnapshot:
        shares = (
            self.current_shares_outstanding,
            self.prior_year_shares_outstanding,
        )
        if (shares[0] is None) != (shares[1] is None):
            raise ValueError("current and prior-year shares must be supplied together")
        cash_flow = (self.cash_and_equivalents, self.quarterly_operating_cash_flow)
        if (cash_flow[0] is None) != (cash_flow[1] is None):
            raise ValueError("cash and quarterly operating cash flow must be supplied together")
        return self


class DilutionEvaluationInput(StrictFrozenModel):
    """All inputs required for a repeatable, network-free evaluation."""

    symbol: Symbol
    as_of: date
    filings: tuple[SecFiling, ...] = ()
    facts: CompanyFactsSnapshot | None = None


class RiskEvidence(StrictFrozenModel):
    """One independently explainable contribution to the risk score."""

    code: NonEmptyText
    source: EvidenceSource
    points: int = Field(gt=0, le=100)
    description: NonEmptyText
    observed_at: date
    accession_number: AccessionNumber | None = None
    source_accessions: tuple[AccessionNumber, ...] = ()


class DilutionMetrics(StrictFrozenModel):
    """Derived values exposed for audit and alert rendering."""

    share_growth_percent: Decimal | None = None
    cash_runway_quarters: Decimal | None = None


class DilutionAssessment(StrictFrozenModel):
    """Deterministic SEC dilution result suitable for publication as an event."""

    symbol: Symbol
    as_of: date
    score: int = Field(ge=0, le=100)
    severity: RiskSeverity
    evidence: tuple[RiskEvidence, ...]
    reasons: tuple[NonEmptyText, ...]
    metrics: DilutionMetrics
    analyzed_filing_count: int = Field(ge=0)
    ignored_stale_filing_count: int = Field(ge=0)
