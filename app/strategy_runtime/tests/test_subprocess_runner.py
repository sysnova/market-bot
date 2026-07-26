from datetime import UTC, datetime
from decimal import Decimal

from app.contracts import (
    EvaluationContext,
    MarketSession,
    RuleResult,
    RuleStatus,
    StrictFrozenModel,
)
from app.strategy_runtime import SubprocessRuleRunner


class Params(StrictFrozenModel):
    marker: int = 1


def passes(context: EvaluationContext, _parameters: Params) -> RuleResult:
    return RuleResult(
        rule_id="passes",
        rule_version="1.0.0",
        status=RuleStatus.PASS,
        evaluated_at=context.as_of,
        score=Decimal("1"),
        reason="ok",
    )


def explodes(_context: EvaluationContext, _parameters: Params) -> RuleResult:
    raise RuntimeError("boom")


def hangs(_context: EvaluationContext, _parameters: Params) -> RuleResult:
    while True:
        pass


def invalid(_context: EvaluationContext, _parameters: Params) -> RuleResult:
    return "wrong"  # type: ignore[return-value]


def context() -> EvaluationContext:
    return EvaluationContext(
        symbol="TEST",
        timeframe="1m",
        as_of=datetime(2026, 1, 1, tzinfo=UTC),
        market_session=MarketSession.CONTINUOUS,
    )


def test_runner_returns_valid_result() -> None:
    result = SubprocessRuleRunner(timeout_seconds=1).run(
        passes, context(), Params(), rule_id="passes", rule_version="1.0.0"
    )
    assert result.status is RuleStatus.PASS


def test_runner_isolates_exception_invalid_output_and_hard_timeout() -> None:
    runner = SubprocessRuleRunner(timeout_seconds=0.15)
    exception = runner.run(explodes, context(), Params(), rule_id="x", rule_version="1.0.0")
    malformed = runner.run(invalid, context(), Params(), rule_id="x", rule_version="1.0.0")
    timeout = runner.run(hangs, context(), Params(), rule_id="x", rule_version="1.0.0")
    assert exception.status is RuleStatus.ERROR and exception.error_code == "RULE_EXCEPTION"
    assert malformed.status is RuleStatus.ERROR and malformed.error_code == "INVALID_OUTPUT"
    assert timeout.status is RuleStatus.ERROR and timeout.error_code == "RULE_TIMEOUT"
