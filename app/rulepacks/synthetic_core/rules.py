"""Pure, synchronous rules used to validate the rule platform."""

from __future__ import annotations

from decimal import Decimal

from app.contracts import (
    EvaluationContext,
    RuleOutputValue,
    RuleResult,
    RuleStatus,
)

from .parameters import (
    ExceptionParameters,
    MultiplyParameters,
    ReadNumberParameters,
    ThresholdV1Parameters,
    ThresholdV2Parameters,
    TimeoutParameters,
)


def _result(
    context: EvaluationContext,
    *,
    rule_id: str,
    version: str,
    status: RuleStatus,
    score: Decimal | None,
    reason: str,
    outputs: tuple[RuleOutputValue, ...] = (),
    error_code: str | None = None,
    error_message: str | None = None,
) -> RuleResult:
    return RuleResult(
        rule_id=rule_id,
        rule_version=version,
        status=status,
        evaluated_at=context.as_of,
        score=score,
        reason=reason,
        outputs=outputs,
        error_code=error_code,
        error_message=error_message,
        duration_ms=Decimal("0"),
    )


def read_number(context: EvaluationContext, parameters: ReadNumberParameters) -> RuleResult:
    value_by_name = {item.name: item.value for item in context.values}
    value = value_by_name.get(parameters.source)
    if isinstance(value, bool) or not isinstance(value, (Decimal, int)):
        return _result(
            context,
            rule_id="synthetic.read_number",
            version="1.0.0",
            status=RuleStatus.ERROR,
            score=None,
            reason=f"context value {parameters.source!r} is absent or not an exact number",
            error_code="INVALID_NUMBER",
            error_message="source must contain a Decimal or integer",
        )
    number = value if isinstance(value, Decimal) else Decimal(value)
    return _result(
        context,
        rule_id="synthetic.read_number",
        version="1.0.0",
        status=RuleStatus.PASS,
        score=Decimal("1"),
        reason=f"read exact number from {parameters.source}",
        outputs=(RuleOutputValue(name="number", value=number),),
    )


def multiply(context: EvaluationContext, parameters: MultiplyParameters) -> RuleResult:
    product = parameters.value * parameters.factor
    return _result(
        context,
        rule_id="synthetic.multiply",
        version="1.0.0",
        status=RuleStatus.PASS,
        score=Decimal("1"),
        reason="multiplied exact decimal values",
        outputs=(RuleOutputValue(name="product", value=product),),
    )


def threshold_v1(context: EvaluationContext, parameters: ThresholdV1Parameters) -> RuleResult:
    passed = parameters.value >= parameters.minimum
    return _result(
        context,
        rule_id="synthetic.threshold",
        version="1.0.0",
        status=RuleStatus.PASS if passed else RuleStatus.FAIL,
        score=Decimal("1") if passed else Decimal("0"),
        reason="value meets minimum" if passed else "value is below minimum",
        outputs=(RuleOutputValue(name="matched", value=passed),),
    )


def threshold_v2(context: EvaluationContext, parameters: ThresholdV2Parameters) -> RuleResult:
    passed = parameters.lower <= parameters.value <= parameters.upper
    return _result(
        context,
        rule_id="synthetic.threshold",
        version="2.0.0",
        status=RuleStatus.PASS if passed else RuleStatus.FAIL,
        score=Decimal("1") if passed else Decimal("0"),
        reason="value is inside inclusive range" if passed else "value is outside inclusive range",
        outputs=(RuleOutputValue(name="matched", value=passed),),
    )


def raise_exception(
    _context: EvaluationContext, parameters: ExceptionParameters
) -> RuleResult:
    raise RuntimeError(parameters.message)


def never_returns(_context: EvaluationContext, _parameters: TimeoutParameters) -> RuleResult:
    """Spin forever so the runtime can prove hard subprocess termination."""

    while True:
        pass
