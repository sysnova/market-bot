"""V1.2: native daily/Swing confirmations can arm a retained ASTX LONG."""

from datetime import UTC, datetime, time, timedelta
from decimal import Decimal

from app.common.canonical import sha256_digest
from app.common.market_session import is_regular_session
from app.contracts import (
    AnalysisHorizon,
    EntryMaturityLevel,
    EntrySignal,
    EntrySignalFamily,
    OrderFlowState,
    PatternDirection,
    SwingTradeMaturity,
)
from app.contracts._base import StrictFrozenModel
from app.contracts.leveraged_thesis import (
    LeveragedExposure,
    LeveragedThesisAssessment,
    LeveragedThesisState,
)

from .engine import _NEW_YORK  # pyright: ignore[reportPrivateUsage]
from .v11 import (
    DeferredShortState,
    InstrumentEntryEvidence,
    LeveragedThesisEngineV11,
    PricePoint,
    deferred_event_id,
)


class LongIntent(StrictFrozenModel):
    signal: EntrySignal
    instrument: str
    expires_at: datetime


class DeferredLongState(StrictFrozenModel):
    intent: LongIntent | None = None
    underlying: OrderFlowState | None = None
    instrument: OrderFlowState | None = None
    prices: tuple[PricePoint, ...] = ()
    entry_evidence: InstrumentEntryEvidence | None = None
    assessment: LeveragedThesisAssessment | None = None
    published_signature: str | None = None


class LeveragedThesisEngineV12(LeveragedThesisEngineV11):
    engine_version = "1.2.0"

    def advance_long(
        self,
        state: DeferredLongState,
        *,
        now: datetime,
        signal: EntrySignal | None = None,
        flow: OrderFlowState | None = None,
    ) -> DeferredLongState:
        if signal is not None:
            intent = self._long_intent(signal, now)
            previous = state.intent
            if intent is not None and (
                previous is None
                or previous.expires_at <= signal.created_at
                or (
                    state.assessment is not None
                    and state.assessment.state is LeveragedThesisState.CANCELLED
                    and previous.signal.setup_id != signal.setup_id
                    and previous.signal.created_at < signal.created_at
                )
            ):
                state = state.model_copy(
                    update={"intent": intent, "assessment": None, "published_signature": None}
                )
        if flow is not None and flow.occurred_at <= now:
            if flow.symbol == "ASTS" and (
                state.underlying is None or flow.occurred_at > state.underlying.occurred_at
            ):
                state = state.model_copy(update={"underlying": flow})
            elif flow.symbol == "ASTX" and (
                state.instrument is None or flow.occurred_at > state.instrument.occurred_at
            ):
                prices = state.prices
                if self._execution_quote_ready(flow) and flow.mid_price is not None:
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
                reason = self._long_instrument_reason(state, now)
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
            if (
                state.assessment.state is LeveragedThesisState.BUY_CONFIRMED
                and not self._long_invalidated(state)
            ):
                return state
        status, reason = self._long_status(state, now)
        underlying, instrument = state.underlying, state.instrument
        evidence = state.entry_evidence
        evidence_reasons = (
            (
                f"instrument_entry_at:{evidence.confirmed_at.isoformat()}",
                f"instrument_entry_flow:{evidence.source_flow_id}",
                f"instrument_entry_reason:{evidence.reason}",
            )
            if evidence is not None and reason == "instrument_prior_entry_confirmed"
            else ()
        )
        digest = sha256_digest(
            {
                "intent": intent,
                "underlying": underlying,
                "instrument": instrument,
                "entry_evidence": evidence,
                "state": status,
                "reason": reason,
                "at": now,
            }
        )
        source = intent.signal
        swing_analysis = next(
            (item for item in source.entry_analyses if item.horizon is AnalysisHorizon.SWING), None
        )
        assessment = LeveragedThesisAssessment(
            assessment_id=deferred_event_id(now, digest),
            underlying_symbol=source.symbol,
            instrument_symbol=intent.instrument,
            occurred_at=now,
            expires_at=max(now + timedelta(seconds=1), intent.expires_at),
            engine_version=self.engine_version,
            state=status,
            direction=PatternDirection.BULLISH,
            exposure=LeveragedExposure.LONG_2X,
            underlying_price=underlying.current_price if underlying else source.entry_price,
            instrument_bid=instrument.bid_price if instrument else None,
            instrument_ask=instrument.ask_price if instrument else None,
            spread_bps=instrument.spread_bps if instrument else None,
            underlying_flow_state=underlying.state if underlying else None,
            underlying_flow_confidence=underlying.confidence if underlying else None,
            instrument_flow_state=instrument.state if instrument else None,
            instrument_flow_confidence=instrument.confidence if instrument else None,
            source_entry_signal_id=source.signal_id,
            source_analysis_id=swing_analysis.analysis_id if swing_analysis else None,
            structure_score=swing_analysis.score if swing_analysis else None,
            source_underlying_flow_state_id=underlying.state_id if underlying else None,
            source_instrument_flow_state_id=instrument.state_id if instrument else None,
            reasons=(
                "confirmed_swing_long_retained",
                f"long_signal:{source.signal_id}",
                f"underlying_entry_family:{source.family.value}",
                f"underlying_invalidation:{source.invalidation}",
                reason,
                *evidence_reasons,
            ),
            context_hash=f"sha256:{digest}",
        )
        return state.model_copy(update={"assessment": assessment})

    def _long_intent(self, signal: EntrySignal, now: datetime) -> LongIntent | None:
        native_swing = (
            signal.family is EntrySignalFamily.SWING_TRADE
            and signal.swing_trade_maturity in {SwingTradeMaturity.ST3, SwingTradeMaturity.ST4}
        )
        core_swing = (
            signal.family in {EntrySignalFamily.CORE_ENTRY, EntrySignalFamily.CORE_RECOVERY}
            and signal.maturity
            in {EntryMaturityLevel.L2, EntryMaturityLevel.L3, EntryMaturityLevel.L4}
            and AnalysisHorizon.SWING in signal.horizons
        )
        if (
            signal.symbol != "ASTS"
            or not (native_swing or core_swing)
            or signal.created_at > now
            or not is_regular_session(signal.created_at)
            or signal.invalidation is None
            or signal.invalidation >= signal.entry_price
            or "swing_trade_rebound_exit" in signal.reasons
        ):
            return None
        close = datetime.combine(
            signal.created_at.astimezone(_NEW_YORK).date(), time(16), _NEW_YORK
        ).astimezone(UTC)
        if now >= close:
            return None
        return LongIntent(signal=signal, instrument="ASTX", expires_at=close)

    @staticmethod
    def _long_invalidated(state: DeferredLongState) -> bool:
        intent, underlying = state.intent, state.underlying
        return (
            intent is not None
            and underlying is not None
            and underlying.occurred_at >= intent.signal.created_at
            and intent.signal.invalidation is not None
            and underlying.current_price <= intent.signal.invalidation
        )

    def _long_instrument_reason(self, state: DeferredLongState, now: datetime) -> str | None:
        return self._instrument_entry_reason(
            DeferredShortState(
                instrument=state.instrument,
                prices=state.prices,
                entry_evidence=state.entry_evidence,
            ),
            now,
        )

    def _long_status(
        self, state: DeferredLongState, now: datetime
    ) -> tuple[LeveragedThesisState, str]:
        assert state.intent is not None
        intent, underlying, flow = state.intent, state.underlying, state.instrument
        armed, cancelled = LeveragedThesisState.STRUCTURE_ARMED, LeveragedThesisState.CANCELLED
        if self._long_invalidated(state):
            return cancelled, "long_published_invalidation_reached"
        if now >= intent.expires_at:
            return cancelled, "long_session_expired"
        if not is_regular_session(now):
            return armed, "regular_session_required"
        if (
            underlying is None
            or not intent.signal.created_at <= underlying.occurred_at <= now
            or now - underlying.occurred_at > timedelta(minutes=1)
        ):
            return armed, "underlying_price_pending_or_stale"
        if (
            flow is None
            or flow.symbol != intent.instrument
            or not 0 <= (now - flow.occurred_at).total_seconds() <= 4
            or not self._execution_quote_ready(flow)
            or flow.quote_age_ms is None
            or flow.quote_age_ms + Decimal(str((now - flow.occurred_at).total_seconds() * 1000))
            > self._maximum_quote_age_ms
        ):
            return armed, "instrument_quote_pending_or_stale"
        if flow.spread_bps is None or flow.spread_bps > self._maximum_spread_bps:
            return armed, "instrument_spread_too_wide"
        reason = self._long_instrument_reason(state, now)
        if reason is not None:
            return LeveragedThesisState.BUY_CONFIRMED, reason
        evidence = state.entry_evidence
        if (
            evidence is not None
            and evidence.symbol == intent.instrument
            and evidence.confirmed_at.astimezone(_NEW_YORK).date()
            == intent.signal.created_at.astimezone(_NEW_YORK).date()
            and timedelta(0) <= now - evidence.confirmed_at <= timedelta(minutes=30)
        ):
            return LeveragedThesisState.BUY_CONFIRMED, "instrument_prior_entry_confirmed"
        return armed, "instrument_buy_flow_or_price_rise_pending"
