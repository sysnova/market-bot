"""Daily gate diagnostics and a separate, non-executable tactical SHORT observer."""

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from app.common.market_session import is_regular_session
from app.contracts import (
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    ExecutionQuoteSnapshot,
    NamedValue,
    OrderFlowState,
    OrderFlowStateKind,
    PatternDirection,
    ShortGate,
    ShortObservation,
    StrictFrozenModel,
    SupportAssessment,
    SupportState,
)

from .v11 import PricePoint
from .v13 import LeveragedThesisEngineV13

_NY = ZoneInfo("America/New_York")
_BUY = {OrderFlowStateKind.BUY_PRESSURE, OrderFlowStateKind.BUY_ABSORPTION}
_REACTION = {SupportState.RECLAIMED, SupportState.STRUCTURE_CONFIRMED}


@dataclass(frozen=True, slots=True)
class ShortObservationContext:
    symbol: str
    now: datetime
    intraday: AnalysisResult | None = None
    swing: AnalysisResult | None = None
    support: SupportAssessment | None = None
    underlying_quote: ExecutionQuoteSnapshot | None = None
    instrument_quote: ExecutionQuoteSnapshot | None = None
    instrument_flow: OrderFlowState | None = None
    instrument_analysis: AnalysisResult | None = None


class TacticalIntent(StrictFrozenModel):
    setup_id: str
    created_at: datetime
    expires_at: datetime
    invalidation: Decimal
    objective: Decimal
    reference: Decimal
    terminal: Literal["INVALIDATED", "EXPIRED", "OBJECTIVE_REACHED"] | None = None


class TacticalState(StrictFrozenModel):
    intent: TacticalIntent | None = None
    prices: tuple[PricePoint, ...] = ()


class LeveragedThesisEngineV14(LeveragedThesisEngineV13):
    engine_version = "1.4.0"
    observation_minimum_rr = Decimal("1.0")

    def inspect_daily(self, context: ShortObservationContext) -> ShortObservation:
        pair = self.pair_for_underlying(context.symbol)
        if pair is None:
            raise ValueError("underlying is outside leveraged scope")
        swing, intraday, now = context.swing, context.intraday, context.now
        sm = _metrics(swing)
        structure = bool(
            swing
            and _fresh(swing.as_of, now, timedelta(hours=8))
            and swing.symbol == context.symbol
            and swing.horizon is AnalysisHorizon.SWING
            and swing.direction is PatternDirection.BEARISH
            and swing.verdict in {AnalysisVerdict.CAUTION, AnalysisVerdict.AVOID}
            and sm.get("short_structure_gate_passed") is True
        )
        gates = [
            _gate("daily_structure", structure, "daily_structure_pending"),
            _gate("timing", _timing(context), "mature_timing_pending"),
        ]
        alert = None
        if swing is not None and intraday is not None:
            alert = self.evaluate_short(
                swing=swing, intraday=intraday, support=context.support, now=now
            )
        ready = alert is not None and "short_entry_confirmed" in alert.reasons
        gates.append(
            _gate(
                "daily_decision", ready, alert.reasons[0] if alert else "daily_decision_unavailable"
            )
        )
        return ShortObservation(
            symbol=context.symbol,
            instrument_symbol=pair.bearish_instrument,
            occurred_at=now,
            engine_version=self.engine_version,
            route="SHORT_DAILY",
            status="READY" if all(g.passed for g in gates) else "BLOCKED",
            gates=tuple(gates),
        )

    def observe_tactical(
        self, state: TacticalState, context: ShortObservationContext
    ) -> tuple[TacticalState, ShortObservation]:
        pair = self.pair_for_underlying(context.symbol)
        if pair is None:
            raise ValueError("underlying is outside leveraged scope")
        now = context.now
        timing = _timing(context)
        structural, support_levels, reaction = _supports(context)
        m = _metrics(context.intraday)
        reference, stop, target = (
            _decimal(m.get(key))
            for key in ("reference_price", "invalidation_level", "objective_level")
        )
        valid_levels = bool(reference and stop and target and stop > reference > target)
        intent = state.intent
        # A new impulse must occur after the retained observation window, not be a replay.
        if (
            timing
            and valid_levels
            and structural
            and (
                intent is None
                or (context.intraday is not None and context.intraday.as_of >= intent.expires_at)
            )
        ):
            assert context.intraday is not None and stop and target and reference
            at = context.intraday.as_of
            below_reference = [p for p in support_levels if p < reference]
            if below_reference:
                target = max(target, max(below_reference))
            close = datetime.combine(now.astimezone(_NY).date(), time(16), _NY).astimezone(UTC)
            intent = TacticalIntent(
                setup_id=f"tactical:{context.symbol}:{at.isoformat()}",
                created_at=at,
                expires_at=min(at + timedelta(minutes=15), close),
                reference=reference,
                invalidation=stop,
                objective=target,
            )
        uq, iq = context.underlying_quote, context.instrument_quote
        underlying_ok = _quote_ready(uq, context.symbol, now)
        prices, instrument_gates, instrument_rr, confirmation = self._instrument_evidence(
            state.prices, context, pair.bearish_instrument
        )
        if intent and intent.terminal is None:
            terminal = None
            if now >= intent.expires_at or not is_regular_session(now):
                terminal = "EXPIRED"
            elif underlying_ok and uq is not None:
                if uq.quote.ask_price >= intent.invalidation:
                    terminal = "INVALIDATED"
                elif uq.quote.ask_price <= intent.objective:
                    terminal = "OBJECTIVE_REACHED"
            if terminal:
                intent = intent.model_copy(update={"terminal": terminal})
        state = TacticalState(intent=intent, prices=prices)
        gates = [
            _gate("session", is_regular_session(now), "regular_session_required"),
            _gate(
                "timing",
                timing or (intent is not None and intent.terminal is None),
                "mature_timing_pending",
            ),
            _gate(
                "intent",
                intent is not None and intent.terminal is None,
                "intent_unavailable_or_terminal",
            ),
            _gate("underlying_quote", underlying_ok, "underlying_quote_unavailable_or_stale"),
        ]
        gates.append(_gate("structural_data", structural, "structural_support_data_missing"))
        gates.append(_gate("support_reaction", not reaction, "confirmed_support_reaction"))
        rr = None
        nearest = None
        effective_target = intent.objective if intent else None
        if intent and underlying_ok and uq is not None:
            bid, spread = uq.quote.bid_price, uq.quote.spread
            below = [p for p in support_levels if p < bid]
            nearest = max(below) if below else None
            effective_target = max(intent.objective, nearest) if nearest else intent.objective
            if intent.invalidation > bid:
                rr = (bid - effective_target - spread) / (intent.invalidation - bid)
        gates.append(
            _gate(
                "remaining_rr",
                rr is not None and rr >= self.observation_minimum_rr,
                "remaining_reward_risk_unavailable"
                if rr is None
                else "remaining_reward_risk_insufficient",
            )
        )
        gates.extend(instrument_gates)
        status = (
            intent.terminal
            if intent and intent.terminal
            else ("READY" if all(g.passed for g in gates) else "BLOCKED")
        )
        values: dict[str, object] = {
            "reference": intent.reference if intent else reference,
            "invalidation": intent.invalidation if intent else None,
            "objective": intent.objective if intent else None,
            "effective_objective": effective_target,
            "nearest_support": nearest,
            "minimum_remaining_rr": self.observation_minimum_rr,
            "instrument_confirmation": confirmation,
            "swing_direction": context.swing.direction.value if context.swing else None,
            "analysis_as_of": context.intraday.as_of if context.intraday else None,
        }
        for prefix, snapshot in (("underlying", uq), ("instrument", iq)):
            if snapshot:
                q = snapshot.quote
                values.update(
                    {
                        f"{prefix}_quote_at": q.occurred_at,
                        f"{prefix}_quote_received_at": q.received_at,
                        f"{prefix}_quote_published_at": snapshot.published_at,
                        f"{prefix}_quote_effective_age_ms": Decimal(
                            str((now - q.occurred_at).total_seconds())
                        )
                        * 1000,
                        f"{prefix}_bid": q.bid_price,
                        f"{prefix}_ask": q.ask_price,
                    }
                )
        report = ShortObservation(
            symbol=context.symbol,
            instrument_symbol=pair.bearish_instrument,
            occurred_at=now,
            engine_version=self.engine_version,
            route="SHORT_TACTICAL",
            status=status,
            setup_id=intent.setup_id if intent else None,
            gates=tuple(gates),
            remaining_reward_risk=rr,
            instrument_reward_risk=instrument_rr,
            metrics=tuple(NamedValue(name=k, value=v) for k, v in values.items()),
        )
        return state, report

    def _instrument_evidence(
        self,
        prices: tuple[PricePoint, ...],
        context: ShortObservationContext,
        instrument: str,
    ) -> tuple[tuple[PricePoint, ...], list[ShortGate], Decimal | None, str | None]:
        now, iq = context.now, context.instrument_quote
        gates: list[ShortGate] = []
        instrument_ok = _quote_ready(iq, instrument, now)
        if instrument_ok and iq is not None:
            at = iq.quote.occurred_at
            if not prices or at > prices[-1].at:
                cutoff = at - timedelta(minutes=3, seconds=5)
                prices = tuple(p for p in prices if p.at >= cutoff)
                prices = (*prices, PricePoint(at=at, price=iq.quote.mid_price))[-190:]
        gates.append(
            _gate("instrument_quote", instrument_ok, "instrument_quote_unavailable_or_stale")
        )
        flow = context.instrument_flow
        buy = bool(
            flow
            and flow.symbol == instrument
            and _fresh(flow.occurred_at, now, timedelta(seconds=4))
            and flow.state in _BUY
            and flow.confidence >= Decimal(".65")
            and flow.data_quality >= Decimal(".70")
        )
        rising = False
        if instrument_ok and iq is not None:
            cutoff = iq.quote.occurred_at - timedelta(minutes=3)
            anchors = [
                p
                for p in prices
                if cutoff - timedelta(seconds=5) <= p.at <= cutoff
                and p.at.astimezone(_NY).date() == now.astimezone(_NY).date()
            ]
            rising = bool(anchors and iq.quote.mid_price > anchors[-1].price)
        gates.append(
            _gate("instrument_confirmation", buy or rising, "instrument_confirmation_pending")
        )
        ia = context.instrument_analysis
        im = _metrics(ia)
        istop, itarget = (_decimal(im.get(k)) for k in ("invalidation_level", "objective_level"))
        instrument_rr = None
        if (
            ia
            and ia.symbol == instrument
            and ia.horizon is AnalysisHorizon.INTRADAY
            and ia.direction is PatternDirection.BULLISH
            and is_regular_session(ia.as_of)
            and timedelta(minutes=1) <= now - ia.as_of <= timedelta(minutes=2)
            and instrument_ok
            and iq
            and istop
            and itarget
            and istop < iq.quote.bid_price <= iq.quote.ask_price < itarget
        ):
            instrument_rr = (itarget - iq.quote.ask_price - iq.quote.spread) / (
                iq.quote.ask_price - istop
            )
        gates.append(
            _gate(
                "instrument_rr",
                instrument_rr is not None and instrument_rr >= self.observation_minimum_rr,
                "instrument_levels_or_reward_risk_pending",
            )
        )
        confirmation = "buy_flow" if buy else "price_rise_3m" if rising else None
        return prices, gates, instrument_rr, confirmation


def _gate(name: str, passed: bool, reason: str) -> ShortGate:
    return ShortGate(name=name, passed=passed, reason="passed" if passed else reason)


def _fresh(at: datetime, now: datetime, age: timedelta) -> bool:
    return timedelta(0) <= now - at <= age


def _metrics(analysis: AnalysisResult | None) -> dict[str, object]:
    return {m.name: m.value for m in analysis.metrics} if analysis else {}


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = Decimal(str(value))
        return number if number.is_finite() and number > 0 else None
    except ArithmeticError, ValueError:
        return None


def _timing(c: ShortObservationContext) -> bool:
    a = c.intraday
    m = _metrics(a)
    return bool(
        a
        and a.symbol == c.symbol
        and a.horizon is AnalysisHorizon.INTRADAY
        and is_regular_session(c.now)
        and is_regular_session(a.as_of)
        and timedelta(minutes=1) <= c.now - a.as_of <= timedelta(minutes=2)
        and a.direction is PatternDirection.BEARISH
        and a.verdict is AnalysisVerdict.FAVORABLE
        and m.get("setup") in {"bearish_breakdown", "bearish_vwap_rejection"}
        and m.get("short_mature_confirmation_gate_passed") is True
    )


def _quote_ready(snapshot: ExecutionQuoteSnapshot | None, symbol: str, now: datetime) -> bool:
    if snapshot is None:
        return False
    q = snapshot.quote
    return (
        q.symbol == symbol
        and snapshot.published_at <= now
        and _fresh(q.occurred_at, now, timedelta(seconds=2))
        and is_regular_session(q.occurred_at)
        and q.bid_size > 0
        and q.ask_size > 0
        and q.spread >= 0
        and q.spread / q.mid_price * 10000 <= 35
    )


def _supports(c: ShortObservationContext) -> tuple[bool, list[Decimal], bool]:
    levels: list[Decimal] = []
    if (
        c.swing
        and c.swing.symbol == c.symbol
        and c.swing.horizon is AnalysisHorizon.SWING
        and _fresh(c.swing.as_of, c.now, timedelta(hours=8))
    ):
        level = _decimal(_metrics(c.swing).get("structural_support"))
        if level:
            levels.append(level)
    s = c.support
    reaction = False
    if (
        s
        and s.symbol == c.symbol
        and s.data_as_of is not None
        and s.data_as_of <= c.now
        and _fresh(s.assessed_at or s.occurred_at, c.now, timedelta(hours=8))
    ):
        levels.extend(p.price for p in s.structural_supports)
        # A broad daily first-touch zone is context, not zero-distance tactical support.
        if s.zone_low and s.state not in {SupportState.INVALIDATED, SupportState.EXPIRED}:
            levels.append(s.zone_low)
        if c.underlying_quote and s.zone_low and s.zone_high:
            price = c.underlying_quote.quote.bid_price
            reaction = s.state in _REACTION and s.zone_low <= price <= s.zone_high
    return bool(levels), levels, reaction
