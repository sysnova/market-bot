import pytest

from app.contracts.microstructure_events import (
    intraday_opportunity_subject,
    order_flow_state_subject,
    order_flow_support_subject,
    order_flow_transition_subject,
    scalp_assessment_subject,
    scalp_transition_subject,
)


def test_microstructure_subjects_are_versioned_and_symbol_safe() -> None:
    assert order_flow_state_subject("brk.b") == "marketbot.v1.order-flow.state.BRK_B"
    assert order_flow_support_subject("brk.b") == "marketbot.v1.order-flow.support.BRK_B"
    assert order_flow_transition_subject("BUY_PRESSURE", "aapl") == (
        "marketbot.v1.order-flow.transition.BUY_PRESSURE.AAPL"
    )
    assert scalp_assessment_subject("AAPL") == "marketbot.v1.scalp.assessment.AAPL"
    assert scalp_transition_subject("ARMED", "AAPL") == (
        "marketbot.v1.scalp.transition.ARMED.AAPL"
    )
    assert intraday_opportunity_subject("OPEN", "AAPL") == (
        "marketbot.v1.intraday-opportunity.transition.OPEN.AAPL"
    )


def test_microstructure_subjects_reject_blank_tokens() -> None:
    with pytest.raises(ValueError, match="subject token"):
        order_flow_state_subject("  ")
