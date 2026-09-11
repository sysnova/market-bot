from app.contracts import SupportState
from app.integration.support_confirmation_monitor import _color_support_line


def test_only_structural_confirmations_are_green() -> None:
    for state in SupportState:
        rendered = _color_support_line("signal", state, color=True)
        if state in {SupportState.STRUCTURE_CONFIRMED, SupportState.RETEST_CONFIRMED}:
            assert rendered == "\033[1;32msignal\033[0m"
        else:
            assert "\033[1;32m" not in rendered


def test_redirected_output_remains_plain() -> None:
    assert (
        _color_support_line("signal", SupportState.STRUCTURE_CONFIRMED, color=False)
        == "signal"
    )
