from io import StringIO
from types import SimpleNamespace

import pytest

from app.alert_engine.confirmed import BuyMaturity
from app.integration import alert_sounds


def test_patreon_confirmation_uses_the_three_tone_windows_chime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launched: list[tuple[list[str], dict[str, object]]] = []

    monkeypatch.setattr(alert_sounds.shutil, "which", lambda _: "powershell.exe")
    monkeypatch.setattr(
        alert_sounds.subprocess,
        "Popen",
        lambda command, **options: launched.append((command, options)) or SimpleNamespace(),
    )

    assert alert_sounds.play_patreon_confirmation_sound(fallback=StringIO()) is True
    assert len(launched) == 1
    command, options = launched[0]
    script = command[-1]
    assert "Beep(650, 180)" in script
    assert "Beep(850, 180)" in script
    assert "Beep(1100, 300)" in script
    assert options["stdin"] is alert_sounds.subprocess.DEVNULL


def test_patreon_confirmation_falls_back_to_terminal_bell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = StringIO()
    monkeypatch.setattr(alert_sounds.shutil, "which", lambda _: None)

    assert alert_sounds.play_patreon_confirmation_sound(fallback=output) is False
    assert output.getvalue() == "\a"


def test_solid_buy_uses_a_distinct_native_alarm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launched: list[tuple[list[str], dict[str, object]]] = []
    monkeypatch.setattr(alert_sounds.shutil, "which", lambda _: "powershell.exe")
    monkeypatch.setattr(
        alert_sounds.subprocess,
        "Popen",
        lambda command, **options: launched.append((command, options)) or SimpleNamespace(),
    )

    assert alert_sounds.play_solid_buy_sound(fallback=StringIO()) is True
    script = launched[0][0][-1]
    assert "Beep(1200, 220)" in script
    assert "Beep(1600, 500)" in script
    assert script != alert_sounds._PATREON_CONFIRMATION_SCRIPT


def test_aggressive_flow_uses_a_short_two_tone_alarm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launched: list[list[str]] = []
    monkeypatch.setattr(alert_sounds.shutil, "which", lambda _: "powershell.exe")
    monkeypatch.setattr(
        alert_sounds.subprocess,
        "Popen",
        lambda command, **_: launched.append(command) or SimpleNamespace(),
    )

    assert alert_sounds.play_aggressive_flow_sound(fallback=StringIO()) is True
    script = launched[0][-1]
    assert "Beep(1350, 120)" in script
    assert "Beep(1650, 220)" in script
    assert script.count("[console]::Beep") == 2


def test_early_intraday_uses_a_distinct_rising_watch_tone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launched: list[list[str]] = []
    monkeypatch.setattr(alert_sounds.shutil, "which", lambda _: "powershell.exe")
    monkeypatch.setattr(
        alert_sounds.subprocess,
        "Popen",
        lambda command, **_: launched.append(command) or SimpleNamespace(),
    )

    assert alert_sounds.play_early_intraday_sound(fallback=StringIO()) is True
    script = launched[0][-1]
    assert "Beep(720, 140)" in script
    assert "Beep(980, 240)" in script
    assert script != alert_sounds._AGGRESSIVE_FLOW_SCRIPT
    assert script != alert_sounds._SOLID_BUY_SCRIPT


@pytest.mark.parametrize(
    ("play", "expected"),
    (
        (alert_sounds.play_entry_zone_watch_sound, "Beep(820, 260)"),
        (alert_sounds.play_swing_setup_watch_sound, "Beep(660, 180)"),
    ),
)
def test_unconfirmed_candidate_watches_use_single_soft_native_tones(
    monkeypatch: pytest.MonkeyPatch,
    play: object,
    expected: str,
) -> None:
    launched: list[list[str]] = []
    monkeypatch.setattr(alert_sounds.shutil, "which", lambda _: "powershell.exe")
    monkeypatch.setattr(
        alert_sounds.subprocess,
        "Popen",
        lambda command, **_: launched.append(command) or SimpleNamespace(),
    )

    assert play(fallback=StringIO()) is True  # type: ignore[operator]
    script = launched[0][-1]
    assert expected in script
    assert script.count("[console]::Beep") == 1


@pytest.mark.parametrize(
    ("maturity", "tone", "tone_count"),
    (
        (BuyMaturity.TACTICAL_RECOVERY, "Beep(780, 260)", 1),
        (BuyMaturity.SWING_CONFIRMED, "Beep(1150, 320)", 2),
        (BuyMaturity.HIGH_CONVICTION, "Beep(1550, 380)", 3),
        (BuyMaturity.FULLY_MATURED, "Beep(1600, 500)", 3),
    ),
)
def test_each_buy_maturity_has_its_own_native_pattern(
    monkeypatch: pytest.MonkeyPatch,
    maturity: BuyMaturity,
    tone: str,
    tone_count: int,
) -> None:
    launched: list[list[str]] = []
    monkeypatch.setattr(alert_sounds.shutil, "which", lambda _: "powershell.exe")
    monkeypatch.setattr(
        alert_sounds.subprocess,
        "Popen",
        lambda command, **_: launched.append(command) or SimpleNamespace(),
    )

    assert alert_sounds.play_buy_maturity_sound(maturity, fallback=StringIO()) is True
    script = launched[0][-1]
    assert tone in script
    assert script.count("[console]::Beep") == tone_count
