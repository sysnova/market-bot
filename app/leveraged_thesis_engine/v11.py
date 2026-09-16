"""V1.1: retain confirmed SHORT intent independently of instrument entry timing."""

from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID

from app.common.canonical import sha256_digest
from app.common.market_session import is_regular_session
from app.contracts import AlertKind, LocalAlert, OrderFlowState, PatternDirection
from app.contracts._base import PositiveDecimal, StrictFrozenModel
from app.contracts.leveraged_thesis import (
    LeveragedExposure,
    LeveragedThesisAssessment,
    LeveragedThesisState,
)

from .engine import (
    _BUY_FLOW,  # pyright: ignore[reportPrivateUsage]
    _NEW_YORK,  # pyright: ignore[reportPrivateUsage]
    LeveragedThesisEngine,
    _stable_uuid7,  # pyright: ignore[reportPrivateUsage]
)


class ShortIntent(StrictFrozenModel):
    alert: LocalAlert
    instrument: str
    setup_id: str
    invalidation: PositiveDecimal
    reference_price: PositiveDecimal
    expires_at: datetime


class PricePoint(StrictFrozenModel):
    at: datetime
    price: PositiveDecimal


class InstrumentEntryEvidence(StrictFrozenModel):
    symbol: str
    confirmed_at: datetime
    source_flow_id: UUID
    reference_price: PositiveDecimal
    reason: str


class DeferredShortState(StrictFrozenModel):
    entry_evidence: InstrumentEntryEvidence | None = None
    intent: ShortIntent | None = None
    underlying: OrderFlowState | None = None
    instrument: OrderFlowState | None = None
    prices: tuple[PricePoint, ...] = ()
    assessment: LeveragedThesisAssessment | None = None
    published_signature: str | None = None


class LeveragedThesisEngineV11(LeveragedThesisEngine):
    engine_version = "1.1.0"

    def advance_short(
        self,
        state: DeferredShortState,
        *,
        now: datetime,
        alert: LocalAlert | None = None,
        flow: OrderFlowState | None = None,
    ) -> DeferredShortState:
        if alert is not None:
            intent = self._intent(alert, now)
            if intent is not None:
                previous = state.intent
                if previous is None or (
                    previous.expires_at <= alert.created_at
                    or (
                        state.assessment is not None
                        and state.assessment.state is LeveragedThesisState.CANCELLED
                        and previous.setup_id != intent.setup_id
                        and previous.alert.created_at < alert.created_at
                    )
                ):
                    state = state.model_copy(
                        update={"intent": intent, "assessment": None, "published_signature": None}
                    )
        if flow is not None and flow.occurred_at <= now:
            if self.pair_for_underlying(flow.symbol) is not None:
                if state.underlying is None or flow.occurred_at > state.underlying.occurred_at:
                    state = state.model_copy(update={"underlying": flow})
            elif any(pair.bearish_instrument == flow.symbol for pair in self.pairs) and (
                state.instrument is None or flow.occurred_at > state.instrument.occurred_at
            ):
                prices = state.prices
                if self._execution_quote_ready(flow) and flow.mid_price is not None:
                    # One midpoint per second, plus one anchor at the 3-minute boundary.
                    point = PricePoint(at=flow.occurred_at, price=flow.mid_price)
                    if prices and prices[-1].at.replace(microsecond=0) == point.at.replace(
                        microsecond=0
                    ):
                        prices = prices[:-1]
                    prices = (*prices, point)
                    cutoff = point.at - timedelta(minutes=3)
                    before = [item for item in prices if item.at <= cutoff]
                    prices = (*before[-1:], *(item for item in prices if item.at > cutoff))
                state = state.model_copy(update={"instrument": flow, "prices": prices})
                reason = self._instrument_entry_reason(state, now)
                if reason is not None and flow.mid_price is not None:
                    state = state.model_copy(
                        update={
                            "entry_evidence": InstrumentEntryEvidence(
                                symbol=flow.symbol,
                                confirmed_at=flow.occurred_at,
                                source_flow_id=flow.state_id,
                                reference_price=flow.mid_price,
                                reason=reason,
                            )
                        }
                    )
        intent = state.intent
        if intent is None:
            return state
        if state.assessment is not None:
            if state.assessment.state is LeveragedThesisState.CANCELLED:
                return state
            if state.assessment.state is LeveragedThesisState.BUY_CONFIRMED and not (
                state.underlying is not None
                and state.underlying.occurred_at >= intent.alert.created_at
                and state.underlying.current_price >= intent.invalidation
            ):
                return state
        status, reason = self._pending_status(state, now)
        underlying, instrument = state.underlying, state.instrument
        digest = sha256_digest(
            {
                "intent": intent,
                "status": status,
                "reason": reason,
                "underlying": underlying,
                "instrument": instrument,
                "entry_evidence": state.entry_evidence,
                "at": now,
            }
        )
        evidence_reasons: tuple[str, ...] = ()
        if reason == "instrument_prior_entry_confirmed" and state.entry_evidence is not None:
            evidence = state.entry_evidence
            evidence_reasons = (
                f"instrument_entry_at:{evidence.confirmed_at.isoformat()}",
                f"instrument_entry_flow:{evidence.source_flow_id}",
                f"instrument_entry_reason:{evidence.reason}",
            )
        assessment = LeveragedThesisAssessment(
            assessment_id=_stable_uuid7(now, digest),
            underlying_symbol=intent.alert.symbol,
            instrument_symbol=intent.instrument,
            occurred_at=now,
            expires_at=max(now + timedelta(seconds=1), intent.expires_at),
            engine_version=self.engine_version,
            state=status,
            direction=PatternDirection.BEARISH,
            exposure=LeveragedExposure.INVERSE_2X,
            underlying_price=underlying.current_price if underlying else intent.reference_price,
            instrument_bid=instrument.bid_price if instrument else None,
            instrument_ask=instrument.ask_price if instrument else None,
            spread_bps=instrument.spread_bps if instrument else None,
            underlying_flow_state=underlying.state if underlying else None,
            underlying_flow_confidence=underlying.confidence if underlying else None,
            instrument_flow_state=instrument.state if instrument else None,
            instrument_flow_confidence=instrument.confidence if instrument else None,
            structure_score=next(
                (
                    item.score
                    for item in intent.alert.component_analyses
                    if item.horizon.value == "INTRADAY"
                ),
                intent.alert.score,
            ),
            source_analysis_id=intent.alert.component_analysis_ids[-1],
            source_underlying_flow_state_id=underlying.state_id if underlying else None,
            source_instrument_flow_state_id=instrument.state_id if instrument else None,
            reasons=(
                "confirmed_short_retained",
                f"short_alert:{intent.alert.alert_id}",
                reason,
                *evidence_reasons,
            ),
            context_hash=f"sha256:{digest}",
        )
        return state.model_copy(update={"assessment": assessment})

    def _intent(self, alert: LocalAlert, now: datetime) -> ShortIntent | None:
        pair = self.pair_for_underlying(alert.symbol)
        if (
            pair is None
            or alert.kind is not AlertKind.BEARISH_CONSENSUS
            or "short_entry_confirmed" not in alert.reasons
            or alert.created_at > now
            or not is_regular_session(alert.created_at)
        ):
            return None
        close = datetime.combine(
            alert.created_at.astimezone(_NEW_YORK).date(), time(16), _NEW_YORK
        ).astimezone(UTC)
        if now >= close:
            return None
        metrics = {item.name: item.value for item in alert.metrics}
        try:
            level = Decimal(str(metrics["short_invalidation"]))
            price = Decimal(str(metrics["short_entry_price"]))
            setup = str(metrics["short_setup_id"])
        except KeyError, ValueError, ArithmeticError:
            return None
        if not level.is_finite() or not price.is_finite() or not 0 < price < level:
            return None
        return ShortIntent(
            alert=alert,
            instrument=pair.bearish_instrument,
            setup_id=setup,
            invalidation=level,
            reference_price=price,
            expires_at=close,
        )

    def _pending_status(
        self, state: DeferredShortState, now: datetime
    ) -> tuple[LeveragedThesisState, str]:
        assert state.intent is not None
        intent, underlying, flow = state.intent, state.underlying, state.instrument
        armed, cancelled = LeveragedThesisState.STRUCTURE_ARMED, LeveragedThesisState.CANCELLED
        if (
            underlying is not None
            and underlying.occurred_at >= intent.alert.created_at
            and underlying.current_price >= intent.invalidation
        ):
            return cancelled, "short_published_invalidation_reached"
        if now >= intent.expires_at:
            return cancelled, "short_session_expired"
        if not is_regular_session(now):
            return armed, "regular_session_required"
        if (
            underlying is None
            or not intent.alert.created_at <= underlying.occurred_at <= now
            or now - underlying.occurred_at > timedelta(minutes=1)
        ):
            return armed, "underlying_price_pending_or_stale"
        if (
            flow is None
            or flow.symbol != intent.instrument
            or not 0 <= (now - flow.occurred_at).total_seconds() <= 4
        ):
            return armed, "instrument_quote_pending_or_stale"
        if (
            not self._execution_quote_ready(flow)
            or flow.quote_age_ms is None
            or flow.quote_age_ms + Decimal(str((now - flow.occurred_at).total_seconds() * 1000))
            > self._maximum_quote_age_ms
        ):
            return armed, "instrument_quote_pending_or_stale"
        if flow.spread_bps is None or flow.spread_bps > self._maximum_spread_bps:
            return armed, "instrument_spread_too_wide"
        reason = self._instrument_entry_reason(state, now)
        if reason is not None:
            return LeveragedThesisState.BUY_CONFIRMED, reason
        evidence = state.entry_evidence
        if (
            evidence is not None
            and evidence.symbol == intent.instrument
            and evidence.confirmed_at.astimezone(_NEW_YORK).date()
            == intent.alert.created_at.astimezone(_NEW_YORK).date()
            and timedelta(0) <= now - evidence.confirmed_at <= timedelta(minutes=30)
        ):
            return LeveragedThesisState.BUY_CONFIRMED, "instrument_prior_entry_confirmed"
        return armed, "instrument_buy_flow_or_price_rise_pending"

    def _instrument_entry_reason(self, state: DeferredShortState, now: datetime) -> str | None:
        """Capture entry evidence even before the underlying confirms its SHORT."""
        flow = state.instrument
        if (
            flow is None
            or not is_regular_session(now)
            or not is_regular_session(flow.occurred_at)
            or not 0 <= (now - flow.occurred_at).total_seconds() <= 4
            or not self._execution_quote_ready(flow)
            or flow.quote_age_ms is None
            or flow.quote_age_ms + Decimal(str((now - flow.occurred_at).total_seconds() * 1000))
            > self._maximum_quote_age_ms
            or flow.spread_bps is None
            or flow.spread_bps > self._maximum_spread_bps
        ):
            return None
        if flow.state in _BUY_FLOW and self._flow_ready(flow, as_of=now, maximum_age_ms=4_000):
            return "instrument_buy_flow_confirmed"
        cutoff = flow.occurred_at - timedelta(minutes=3)
        anchors = [
            point
            for point in state.prices
            if cutoff - timedelta(seconds=5) <= point.at <= cutoff
            and point.at.astimezone(_NEW_YORK).date()
            == flow.occurred_at.astimezone(_NEW_YORK).date()
            and is_regular_session(point.at)
        ]
        if anchors and flow.mid_price is not None and flow.mid_price > anchors[-1].price:
            return "instrument_price_rising_3m"
        return None


def deferred_event_id(at: datetime, key: str) -> UUID:
    return _stable_uuid7(at, key)
