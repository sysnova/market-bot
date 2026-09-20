from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.contracts import (
    AlertSeverity,
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    NamedValue,
    PatternDirection,
    TradeSide,
)
from app.entry_opportunity_engine.memory import InMemoryEntryOpportunityStore
from app.entry_opportunity_engine.v23 import EntryOpportunityEngineV23
from app.leveraged_thesis_engine.v11 import DeferredShortState
from app.leveraged_thesis_engine.v13 import LeveragedThesisEngineV13

NOW = datetime(2026, 9, 18, 16, 24, tzinfo=UTC)
HASH = "sha256:" + "1" * 64


def _analysis(
    horizon: AnalysisHorizon,
    *,
    metrics: tuple[NamedValue, ...],
    as_of: datetime,
) -> AnalysisResult:
    return AnalysisResult(
        engine_id=horizon.value.lower(),
        engine_version="1.0.0",
        symbol="ASTS",
        horizon=horizon,
        as_of=as_of,
        verdict=(
            AnalysisVerdict.AVOID
            if horizon is AnalysisHorizon.SWING
            else AnalysisVerdict.FAVORABLE
        ),
        direction=PatternDirection.BEARISH,
        score=Decimal("82"),
        confidence=Decimal("0.82"),
        reasons=("fixture",),
        metrics=metrics,
        context_hash=HASH,
    )


def _swing(*, support: str = "54", atr: str = "3") -> AnalysisResult:
    return _analysis(
        AnalysisHorizon.SWING,
        as_of=NOW - timedelta(minutes=15),
        metrics=(
            NamedValue(name="short_structure_gate_passed", value=True),
            NamedValue(name="short_setup_id", value="swing-short:ASTS:2026-09-18"),
            NamedValue(name="structural_support", value=Decimal(support)),
            NamedValue(name="atr14", value=Decimal(atr)),
        ),
    )


def _intraday(*, entry: str = "58", gate: bool = True) -> AnalysisResult:
    value = Decimal(entry)
    return _analysis(
        AnalysisHorizon.INTRADAY,
        as_of=NOW - timedelta(minutes=1),
        metrics=(
            NamedValue(name="setup", value="bearish_breakdown"),
            NamedValue(name="reference_price", value=value),
            NamedValue(name="invalidation_level", value=value + Decimal("0.50")),
            NamedValue(name="objective_level", value=value - Decimal("0.75")),
            NamedValue(name="short_mature_confirmation_gate_passed", value=gate),
            NamedValue(name="short_confirmation_rule_version", value="1.2.0"),
        ),
    )


def test_leveraged_thesis_confirms_the_scoped_underlying_short() -> None:
    alert = LeveragedThesisEngineV13().evaluate_short(
        swing=_swing(), intraday=_intraday(), now=NOW
    )

    assert alert is not None
    assert alert.title == "ASTS SHORT CONFIRMED"
    assert alert.severity is AlertSeverity.ACTION
    assert "short_entry_confirmed" in alert.reasons
    assert "short_decision_owned_by_leveraged_thesis" in alert.reasons
    metrics = {item.name: item.value for item in alert.metrics}
    assert metrics["short_entry_price"] == Decimal("58")
    assert metrics["short_support_guard_passed"] is True


def test_leveraged_thesis_blocks_short_inside_support_bounce_zone() -> None:
    alert = LeveragedThesisEngineV13().evaluate_short(
        swing=_swing(support="57.27", atr="3.7142"),
        intraday=_intraday(entry="57.935"),
        now=NOW,
    )

    assert alert is not None
    assert alert.title == "ASTS SHORT BLOCKED - SUPPORT"
    assert alert.severity is AlertSeverity.WATCH
    assert "short_entry_confirmed" not in alert.reasons
    assert "short_confirmation_blocked_near_support" in alert.reasons
    metrics = {item.name: item.value for item in alert.metrics}
    assert metrics["short_support_distance_atr"] == Decimal("0.1790")


def test_leveraged_thesis_allows_short_after_decisive_support_break() -> None:
    alert = LeveragedThesisEngineV13().evaluate_short(
        swing=_swing(support="59", atr="3"),
        intraday=_intraday(entry="58"),
        now=NOW,
    )

    assert alert is not None
    assert alert.title == "ASTS SHORT CONFIRMED"
    assert "short_entry_confirmed" in alert.reasons


def test_leveraged_thesis_fails_closed_without_support_data() -> None:
    swing = _swing().model_copy(
        update={
            "metrics": tuple(
                item
                for item in _swing().metrics
                if item.name not in {"structural_support", "atr14"}
            )
        }
    )

    alert = LeveragedThesisEngineV13().evaluate_short(
        swing=swing, intraday=_intraday(), now=NOW
    )

    assert alert is not None
    assert alert.title == "ASTS SHORT BLOCKED - SUPPORT DATA"
    assert "short_support_data_missing" in alert.reasons


def test_leveraged_thesis_requires_fresh_mature_timing() -> None:
    engine = LeveragedThesisEngineV13()

    assert engine.evaluate_short(
        swing=_swing(), intraday=_intraday(gate=False), now=NOW
    ) is None
    assert engine.evaluate_short(
        swing=_swing(),
        intraday=_intraday().model_copy(
            update={"as_of": NOW - timedelta(minutes=3)}
        ),
        now=NOW,
    ) is None


def test_short_decision_identity_is_stable_for_the_same_setup() -> None:
    engine = LeveragedThesisEngineV13()

    first = engine.evaluate_short(swing=_swing(), intraday=_intraday(), now=NOW)
    second = engine.evaluate_short(
        swing=_swing(), intraday=_intraday(), now=NOW + timedelta(seconds=15)
    )

    assert first is not None and second is not None
    assert first.alert_id == second.alert_id
    assert first.deduplication_key == second.deduplication_key


async def test_owned_short_alert_opens_the_underlying_opportunity() -> None:
    alert = LeveragedThesisEngineV13().evaluate_short(
        swing=_swing(), intraday=_intraday(), now=NOW
    )
    store = InMemoryEntryOpportunityStore()
    opportunities = EntryOpportunityEngineV23(
        store=store, short_symbols=("ASTS",), now=lambda: NOW
    )

    assert alert is not None
    events = await opportunities.ingest_alert(alert)
    opportunity = await store.load_active("ASTS")

    assert len(events) == 1
    assert opportunity is not None
    assert opportunity.trade_side is TradeSide.SHORT


def test_owned_short_alert_arms_the_inverse_instrument() -> None:
    engine = LeveragedThesisEngineV13()
    alert = engine.evaluate_short(swing=_swing(), intraday=_intraday(), now=NOW)

    assert alert is not None
    state = engine.advance_short(DeferredShortState(), now=NOW, alert=alert)

    assert state.intent is not None
    assert state.intent.instrument == "ASTN"
    assert state.assessment is not None
