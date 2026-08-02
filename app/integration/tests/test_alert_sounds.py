from io import StringIO
from types import SimpleNamespace

import pytest

from app.integration import alert_sounds


def test_patreon_confirmation_uses_the_three_tone_windows_chime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launched: list[tuple[list[str], dict[str, object]]] = []

    monkeypatch.setattr(alert_sounds.shutil, "which", lambda _: "powershell.exe")
    monkeypatch.setattr(
        alert_sounds.subprocess,
        "Popen",
        lambda command, **options: launched.append((command, options))
        or SimpleNamespace(),
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
