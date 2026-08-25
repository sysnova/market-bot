from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from app.contracts import (
    AlertKind,
    AlertSeverity,
    AnalysisHorizon,
    EntryCloseReason,
    EntryLegStatus,
    EntryOpportunityStatus,
    EntrySignalFamily,
    OrderFlowStateKind,
    PatternDirection,
    SupportState,
    SupportZonePosition,
    new_uuid7,
)
from app.contracts.leveraged_thesis import (
    LeveragedExposure,
    LeveragedThesisAssessment,
    LeveragedThesisState,
)
from app.entry_opportunity_engine import EntryOpportunityEngineV2
from app.entry_opportunity_engine.memory import InMemoryEntryOpportunityStore
from app.integration.engine_assembly import EngineSlot, MarketBotAssembly
from app.integration.entry_signal_adapter import entry_signal_from_leveraged_thesis
from app.integration.leveraged_thesis_composition import (
    build_leveraged_alert,
    leveraged_thesis_publications,
    leveraged_thesis_source_subjects,
)
from app.integration.runtime_process_plan import build_runtime_process_plan

NOW = datetime(2026, 8, 24, 15, tzinfo=UTC)
DEFINITION = Path(__file__).resolve().parents[3] / "configs/marketbot/7.34.0.yaml"


def test_confirmed_and_early_states_materialize_distinct_human_alerts() -> None:
    confirmed = build_leveraged_alert(_assessment(LeveragedThesisState.BUY_CONFIRMED))
    early = build_leveraged_alert(_assessment(LeveragedThesisState.EARLY_FLOW))

    assert confirmed is not None
    assert confirmed.symbol == "ASTN"
    assert confirmed.kind is AlertKind.LEVERAGED_THESIS_BUY
    assert confirmed.severity is AlertSeverity.ACTION
    assert "Soporte: INVALIDATED 64-65" in confirmed.message
    assert early is not None
    assert early.kind is AlertKind.LEVERAGED_THESIS_EARLY
    assert early.severity is AlertSeverity.WATCH


def test_non_actionable_state_does_not_create_buy_notice() -> None:
    assert build_leveraged_alert(_assessment(LeveragedThesisState.BLOCKED)) is None


def test_cancelled_short_materializes_a_critical_cancellation_notice() -> None:
    cancelled = build_leveraged_alert(_assessment(LeveragedThesisState.CANCELLED))

    assert cancelled is not None
    assert cancelled.symbol == "ASTN"
    assert cancelled.kind is AlertKind.LEVERAGED_THESIS_CANCELLED
    assert cancelled.severity is AlertSeverity.CRITICAL
    assert "CANCELACIÓN" in cancelled.title


def test_confirmed_leveraged_buy_becomes_a_trackable_instrument_entry_signal() -> None:
    assessment = _assessment(LeveragedThesisState.BUY_CONFIRMED)

    signal = entry_signal_from_leveraged_thesis(assessment)

    assert signal is not None
    assert signal.family is EntrySignalFamily.LEVERAGED_THESIS
    assert signal.symbol == "ASTN"
    assert signal.entry_price == Decimal("4.92")
    assert signal.zone_low == Decimal("4.91")
    assert signal.zone_high == Decimal("4.92")
    assert signal.invalidation == Decimal("4.7724")
    assert signal.targets == (Decimal("5.2152"),)
    assert signal.horizons == (AnalysisHorizon.INTRADAY,)
    assert assessment.assessment_id in signal.source_event_ids


def test_early_leveraged_watch_does_not_open_an_opportunity() -> None:
    assert (
        entry_signal_from_leveraged_thesis(_assessment(LeveragedThesisState.EARLY_FLOW))
        is None
    )


def test_confirmed_purchase_uses_entry_pipeline_without_duplicate_local_alert() -> None:
    alert, signal = leveraged_thesis_publications(
        _assessment(LeveragedThesisState.BUY_CONFIRMED)
    )

    assert alert is None
    assert signal is not None
    assert signal.family is EntrySignalFamily.LEVERAGED_THESIS


def test_early_watch_remains_an_alert_without_creating_an_entry_signal() -> None:
    alert, signal = leveraged_thesis_publications(
        _assessment(LeveragedThesisState.EARLY_FLOW)
    )

    assert alert is not None
    assert alert.kind is AlertKind.LEVERAGED_THESIS_EARLY
    assert signal is None


async def test_confirmed_leveraged_signal_opens_and_tracks_a_standard_opportunity() -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV2(store=store)
    signal = entry_signal_from_leveraged_thesis(
        _assessment(LeveragedThesisState.BUY_CONFIRMED)
    )
    assert signal is not None

    events = await engine.ingest_signal(signal)
    opportunity = await store.load_active("ASTN")

    assert len(events) == 1
    assert opportunity is not None
    assert opportunity.status is EntryOpportunityStatus.OPEN
    assert opportunity.primary_signal_family is EntrySignalFamily.LEVERAGED_THESIS
    assert opportunity.invalidation == Decimal("4.7724")
    assert opportunity.legs[0].horizon is AnalysisHorizon.INTRADAY
    assert opportunity.legs[0].status is EntryLegStatus.OPEN
    assert opportunity.legs[0].target == Decimal("5.2152")


async def test_sweep_reclaim_cancellation_closes_the_leveraged_opportunity() -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV2(store=store)
    signal = entry_signal_from_leveraged_thesis(
        _assessment(LeveragedThesisState.BUY_CONFIRMED)
    )
    assert signal is not None
    await engine.ingest_signal(signal)
    cancellation = _assessment(LeveragedThesisState.CANCELLED).model_copy(
        update={
            "occurred_at": NOW + timedelta(minutes=1),
            "expires_at": NOW + timedelta(minutes=6),
            "instrument_bid": Decimal("5.10"),
            "instrument_ask": Decimal("5.12"),
            "reasons": ("daily_support_sweep_reclaim_confirmed",),
        }
    )

    events = await engine.ingest_leveraged_cancellation(cancellation)

    assert len(events) == 1
    assert events[0].opportunity.status is EntryOpportunityStatus.CLOSED
    assert events[0].opportunity.close_reason is EntryCloseReason.SWEEP_RECLAIM_CANCELLED
    assert events[0].opportunity.legs[0].status is EntryLegStatus.THESIS_BROKEN
    assert await store.load_active("ASTN") is None


def test_runtime_starts_thesis_after_intraday_and_order_flow() -> None:
    assembly = MarketBotAssembly.from_path(DEFINITION)
    plan = build_runtime_process_plan(assembly.definition, runtime_root=Path(".runtime"))
    process = plan.process("leveraged-thesis")

    assert process.dependencies == ("intraday", "order-flow", "support-confirmation-v0")
    assert EngineSlot.LEVERAGED_THESIS in plan.active_engine_slots


def test_runtime_consumes_only_exact_intraday_and_order_flow_assessment_subjects() -> None:
    engine = MarketBotAssembly.from_path(DEFINITION).build_leveraged_thesis()

    assert leveraged_thesis_source_subjects(engine) == (
        "marketbot.v1.analysis.result.INTRADAY.ASTS",
        "marketbot.v1.analysis.result.INTRADAY.NBIS",
        "marketbot.v1.order-flow.state.ASTS",
        "marketbot.v1.order-flow.state.ASTX",
        "marketbot.v1.order-flow.state.ASTN",
        "marketbot.v1.order-flow.state.NBIS",
        "marketbot.v1.order-flow.state.NBIZ",
        "marketbot.v1.support-confirmation.assessment.ASTS",
        "marketbot.v1.support-confirmation.assessment.NBIS",
    )


def _assessment(state: LeveragedThesisState) -> LeveragedThesisAssessment:
    actionable = state in {
        LeveragedThesisState.EARLY_FLOW,
        LeveragedThesisState.STRUCTURE_ARMED,
        LeveragedThesisState.BUY_CONFIRMED,
        LeveragedThesisState.CANCELLED,
    }
    return LeveragedThesisAssessment(
        underlying_symbol="ASTS",
        instrument_symbol="ASTN" if actionable or state is LeveragedThesisState.BLOCKED else None,
        occurred_at=NOW,
        expires_at=NOW + timedelta(minutes=3),
        engine_version="1.0.0",
        state=state,
        direction=PatternDirection.BEARISH,
        exposure=LeveragedExposure.INVERSE_2X,
        underlying_price=Decimal("62.31"),
        instrument_bid=Decimal("4.91"),
        instrument_ask=Decimal("4.92"),
        spread_bps=Decimal("20.346"),
        underlying_flow_state=OrderFlowStateKind.SELL_PRESSURE,
        underlying_flow_confidence=Decimal("0.81"),
        instrument_flow_state=OrderFlowStateKind.BUY_PRESSURE,
        instrument_flow_confidence=Decimal("0.73"),
        support_state=SupportState.INVALIDATED,
        support_zone_position=SupportZonePosition.BELOW_ZONE,
        support_zone_low=Decimal("64"),
        support_zone_high=Decimal("65"),
        support_invalidation=Decimal("63"),
        support_distance_percent=Decimal("0"),
        support_actionability_score=Decimal("10"),
        structure_score=Decimal("78"),
        source_analysis_id=new_uuid7(),
        reasons=("fixture",),
        context_hash="sha256:" + "e" * 64,
    )
