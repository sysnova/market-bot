from __future__ import annotations

import multiprocessing
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.common.canonical import canonical_json
from app.contracts import ContextValue, EvaluationContext, MarketSession, RuleStatus
from app.rulepacks.synthetic_core.parameters import (
    MultiplyParameters,
    ReadNumberParameters,
    ThresholdV1Parameters,
    ThresholdV2Parameters,
    TimeoutParameters,
)
from app.rulepacks.synthetic_core.provider import get_provider

AS_OF = datetime(2026, 1, 2, 15, 30, tzinfo=UTC)


def _context() -> EvaluationContext:
    return EvaluationContext(
        symbol="SYNTH",
        timeframe="1m",
        as_of=AS_OF,
        market_session=MarketSession.REGULAR,
        values=(ContextValue(name="price", value=Decimal("12.50")),),
    )


def _execute(rule_id: str, version: str, parameters: Any):  # noqa: ANN202
    provider = get_provider()
    registration = provider.resolve(rule_id, version)
    return registration.execute(_context(), parameters)


@pytest.mark.unit
def test_parameter_models_are_strict_and_frozen() -> None:
    with pytest.raises(ValidationError):
        MultiplyParameters(factor="2")  # type: ignore[arg-type]

    params = ReadNumberParameters(source="price")
    with pytest.raises(ValidationError):
        params.source = "other"  # type: ignore[misc]


@pytest.mark.unit
def test_read_number_and_multiply_have_deterministic_golden_results() -> None:
    read_result = _execute(
        "synthetic.read_number", "1.0.0", ReadNumberParameters(source="price")
    )
    multiply_result = _execute(
        "synthetic.multiply",
        "1.0.0",
        MultiplyParameters(value=Decimal("12.50"), factor=Decimal("2")),
    )

    assert read_result.status is RuleStatus.PASS
    assert multiply_result.status is RuleStatus.PASS
    assert canonical_json(read_result) == (
        Path(__file__).parent / "goldens" / "read_number.json"
    ).read_bytes().strip()
    assert canonical_json(multiply_result) == (
        Path(__file__).parent / "goldens" / "multiply.json"
    ).read_bytes().strip()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("version", "parameters", "expected"),
    [
        (
            "1.0.0",
            ThresholdV1Parameters(value=Decimal("10"), minimum=Decimal("10")),
            RuleStatus.PASS,
        ),
        (
            "1.0.0",
            ThresholdV1Parameters(value=Decimal("9.99"), minimum=Decimal("10")),
            RuleStatus.FAIL,
        ),
        (
            "2.0.0",
            ThresholdV2Parameters(
                value=Decimal("10"), lower=Decimal("10"), upper=Decimal("20")
            ),
            RuleStatus.PASS,
        ),
        (
            "2.0.0",
            ThresholdV2Parameters(
                value=Decimal("21"), lower=Decimal("10"), upper=Decimal("20")
            ),
            RuleStatus.FAIL,
        ),
    ],
)
def test_threshold_versions_are_resolved_exactly(
    version: str,
    parameters: ThresholdV1Parameters | ThresholdV2Parameters,
    expected: RuleStatus,
) -> None:
    assert _execute("synthetic.threshold", version, parameters).status is expected


@pytest.mark.unit
def test_exception_rule_raises_intentionally() -> None:
    with pytest.raises(RuntimeError, match="synthetic failure"):
        _execute("synthetic.exception", "1.0.0", {})


def _run_timeout_rule() -> None:
    _execute("synthetic.timeout", "1.0.0", TimeoutParameters())


@pytest.mark.unit
def test_timeout_rule_requires_external_hard_termination() -> None:
    process = multiprocessing.Process(target=_run_timeout_rule)
    process.start()
    process.join(timeout=0.05)
    try:
        assert process.is_alive()
    finally:
        process.terminate()
        process.join(timeout=1)
    assert not process.is_alive()
