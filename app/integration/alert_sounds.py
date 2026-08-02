"""Native alert sounds that remain audible when the tmux runtime is detached."""

from __future__ import annotations

import shutil
import subprocess
from typing import TextIO

_PATREON_CONFIRMATION_SCRIPT = (
    "[console]::Beep(650, 180); "
    "Start-Sleep -Milliseconds 70; "
    "[console]::Beep(850, 180); "
    "Start-Sleep -Milliseconds 70; "
    "[console]::Beep(1100, 300)"
)


def play_patreon_confirmation_sound(*, fallback: TextIO) -> bool:
    """Launch the Patreon three-tone Windows chime or emit a terminal bell."""

    executable = shutil.which("powershell.exe")
    if executable is None:
        _terminal_bell(fallback)
        return False
    try:
        subprocess.Popen(  # noqa: S603 - resolved executable, fixed arguments, no shell.
            [
                executable,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-WindowStyle",
                "Hidden",
                "-Command",
                _PATREON_CONFIRMATION_SCRIPT,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        _terminal_bell(fallback)
        return False
    return True


def _terminal_bell(output: TextIO) -> None:
    output.write("\a")
    output.flush()
