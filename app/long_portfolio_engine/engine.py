"""Stateful confirmation of solid LONG portfolio accumulation entries."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from typing import cast

from app.contracts import (
    AlertKind,
    AlertSeverity,
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    LocalAlert,
    NamedValue,
    PatternDirection,
)

from .models import (
    LongPortfolioPolicy,
    LongPortfolioState,
    LongPortfolioValidationGate,
    PortfolioAllocation,
)

HUNDRED = Decimal("100")


class LongPortfolioEngine:
    """Confirm only durable Long signals and attach bounded allocation guidance."""

    engine_id = "long-portfolio"

    def __init__(
        self,
        policy: LongPortfolioPolicy,
        *,
        restored_states: Iterable[LongPortfolioState] = (),
    ) -> None:
        self._policy = policy
        self._qualified_sessions: dict[str, list[date]] = {}
        self._last_emitted: dict[str, datetime] = {}
        for state in restored_states:
            if (
                state.rule_version != policy.rule_version
                or policy.allocation_for(state.symbol) is None
            ):
                continue
            self._qualified_sessions[state.symbol] = sorted(set(state.qualified_sessions))[
                -policy.minimum_qualified_sessions :
            ]
            if state.last_emitted is not None:
                self._last_emitted[state.symbol] = state.last_emitted

    def ingest(
        self,
        result: AnalysisResult,
        *,
        now: datetime,
        held_quantity: Decimal = Decimal(),
    ) -> LocalAlert | None:
        allocation = self._policy.allocation_for(result.symbol)
        if allocation is None or result.horizon is not AnalysisHorizon.LONG_TERM:
            return None
        self._prune_sessions(result.symbol, now=now)
        if result.as_of > now or now - result.as_of > self._policy.maximum_signal_age:
            return None
        if not self._qualifies(result):
            self._qualified_sessions.pop(result.symbol, None)
            return None

        sessions = self._qualified_sessions.setdefault(result.symbol, [])
        session = result.as_of.date()
        if session not in sessions:
            sessions.append(session)
            sessions.sort()
        sessions[:] = sessions[-self._policy.minimum_qualified_sessions :]
        if len(sessions) < self._policy.minimum_qualified_sessions:
            return None

        previous = self._last_emitted.get(result.symbol)
        if previous is not None and now - previous < self._policy.cooldown:
            return None
        alert = self._build_alert(result, allocation, now, held_quantity)
        if alert is not None:
            self._last_emitted[result.symbol] = now
        return alert

    def state_for(self, symbol: str, *, updated_at: datetime) -> LongPortfolioState | None:
        """Return only the bounded state required to continue after a restart."""

        normalized = symbol.strip().upper()
        if self._policy.allocation_for(normalized) is None:
            return None
        self._prune_sessions(normalized, now=updated_at)
        return LongPortfolioState(
            symbol=normalized,
            rule_version=self._policy.rule_version,
            qualified_sessions=tuple(self._qualified_sessions.get(normalized, ())),
            last_emitted=self._last_emitted.get(normalized),
            updated_at=updated_at,
        )

    def _prune_sessions(self, symbol: str, *, now: datetime) -> None:
        sessions = self._qualified_sessions.get(symbol)
        if sessions is None:
            return
        cutoff = (now - self._policy.maximum_signal_age).date()
        sessions[:] = [session for session in sessions if session >= cutoff]

    def _qualifies(self, result: AnalysisResult) -> bool:
        return all(item.passed for item in self.validation_gates(result))

    def validation_gates(self, result: AnalysisResult) -> tuple[LongPortfolioValidationGate, ...]:
        """Expose the exact solid-entry gates without mutating confirmation state."""

        metrics = _metrics(result)
        risk_flags = _string_tuple(metrics.get("risk_flags"))
        regime = metrics.get("market_regime")
        setup = _decimal(metrics.get("setup_score"))
        entry = _decimal(metrics.get("entry_score"))
        trend = _decimal(metrics.get("trend_template_score"))
        blocked = tuple(sorted(set(risk_flags) & set(self._policy.blocked_risk_flags)))
        return (
            _gate(
                "V",
                result.verdict is AnalysisVerdict.FAVORABLE,
                result.verdict.value,
                "FAVORABLE",
            ),
            _gate(
                "D",
                result.direction is PatternDirection.BULLISH,
                result.direction.value,
                "BULLISH",
            ),
            _minimum_gate("SC", result.score, self._policy.minimum_score),
            _minimum_gate("C", result.confidence, self._policy.minimum_confidence),
            _gate(
                "Z",
                metrics.get("classification") == "buy_zone",
                str(metrics.get("classification")),
                "buy_zone",
            ),
            _minimum_gate("SET", setup, self._policy.minimum_setup_score),
            _minimum_gate("ENT", entry, self._policy.minimum_entry_score),
            _minimum_gate("TR", trend, self._policy.minimum_trend_template_score),
            LongPortfolioValidationGate(
                code="REG",
                passed=regime is None or regime in self._policy.allowed_market_regimes,
                detail=(
                    "allowed"
                    if regime is None or regime in self._policy.allowed_market_regimes
                    else f"{regime} not_allowed"
                ),
            ),
            LongPortfolioValidationGate(
                code="RF",
                passed=not blocked,
                detail="clear" if not blocked else ",".join(blocked),
            ),
        )

    def _build_alert(
        self,
        result: AnalysisResult,
        allocation: PortfolioAllocation,
        now: datetime,
        held_quantity: Decimal,
    ) -> LocalAlert | None:
        metrics = _metrics(result)
        price = _decimal(metrics.get("reference_price"))
        target = _money(
            self._policy.portfolio_capital_usd * allocation.weight_percent / HUNDRED
        )
        current_value = _money(held_quantity * price)
        remaining = _money(max(target - current_value, Decimal()))
        if remaining <= 0:
            return None
        tranche = min(
            _money(target * self._policy.initial_tranche_percent / HUNDRED), remaining
        )
        shares = (tranche / price).quantize(Decimal("1"), rounding=ROUND_DOWN)
        return LocalAlert(
            symbol=result.symbol,
            created_at=now,
            severity=AlertSeverity.ACTION,
            title=f"LONG PORTFOLIO BUY {result.symbol}",
            message=(
                f"Entrada LONG confirmada por {len(self._qualified_sessions[result.symbol])} "
                f"sesiones; faltan USD {remaining} para el objetivo y se sugiere "
                f"una tranche de USD {tranche}."
            ),
            horizons=(AnalysisHorizon.LONG_TERM,),
            component_analysis_ids=(result.analysis_id,),
            component_analyses=(result,),
            metrics=(
                NamedValue(name="current_price", value=price),
                NamedValue(name="buy_zone_low", value=metrics.get("buy_zone_low")),
                NamedValue(name="buy_zone_high", value=metrics.get("buy_zone_high")),
                NamedValue(name="invalidation", value=metrics.get("invalidation")),
                NamedValue(name="target_weight_percent", value=allocation.weight_percent),
                NamedValue(name="target_capital_usd", value=target),
                NamedValue(name="held_quantity", value=held_quantity),
                NamedValue(name="current_holding_value_usd", value=current_value),
                NamedValue(name="remaining_to_target_usd", value=remaining),
                NamedValue(
                    name="suggested_tranche_percent",
                    value=self._policy.initial_tranche_percent,
                ),
                NamedValue(name="suggested_tranche_usd", value=tranche),
                NamedValue(name="suggested_whole_shares", value=shares),
                NamedValue(name="portfolio_capital_usd", value=self._policy.portfolio_capital_usd),
                NamedValue(name="long_horizon_end", value=self._policy.horizon_end),
                NamedValue(name="long_portfolio_rule_version", value=self._policy.rule_version),
            ),
            score=result.score,
            reasons=(
                "long_only_no_swing_dependency",
                "two_session_confirmation",
                "price_inside_weekly_buy_zone",
                "constructive_long_term_trend",
                f"target_weight_percent:{allocation.weight_percent}",
            ),
            deduplication_key=(
                f"long-portfolio:{self._policy.rule_version}:{result.symbol}:"
                f"{int(now.timestamp()) // int(self._policy.cooldown.total_seconds())}"
            ),
            kind=AlertKind.LONG_PORTFOLIO_BUY,
            expires_at=now + self._policy.alert_ttl,
        )


def _metrics(result: AnalysisResult) -> dict[str, object]:
    return {item.name: item.value for item in result.metrics}


def _decimal(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (Decimal, int, str)):
        raise ValueError("required long-term Decimal metric is missing")
    try:
        return Decimal(str(value))
    except ArithmeticError as error:
        raise ValueError("required long-term Decimal metric is malformed") from error


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    values = cast("list[object] | tuple[object, ...]", value)
    if not all(isinstance(item, str) for item in values):
        return ()
    return tuple(str(item) for item in values)


def _gate(code: str, passed: bool, actual: str, required: str) -> LongPortfolioValidationGate:
    return LongPortfolioValidationGate(
        code=code,
        passed=passed,
        detail=actual if passed else f"{actual}!={required}",
    )


def _minimum_gate(code: str, actual: Decimal, minimum: Decimal) -> LongPortfolioValidationGate:
    return LongPortfolioValidationGate(
        code=code,
        passed=actual >= minimum,
        detail=str(actual) if actual >= minimum else f"{actual}<{minimum}",
    )


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
