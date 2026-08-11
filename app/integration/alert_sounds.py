"""Native alert sounds that remain audible when the tmux runtime is detached."""

from __future__ import annotations

import shutil
import subprocess
from typing import TextIO

from app.alert_engine.confirmed import BuyMaturity

_PATREON_CONFIRMATION_SCRIPT = (
    "[console]::Beep(650, 180); "
    "Start-Sleep -Milliseconds 70; "
    "[console]::Beep(850, 180); "
    "Start-Sleep -Milliseconds 70; "
    "[console]::Beep(1100, 300)"
)
_SOLID_BUY_SCRIPT = (
    "[console]::Beep(1200, 220); "
    "Start-Sleep -Milliseconds 90; "
    "[console]::Beep(1200, 220); "
    "Start-Sleep -Milliseconds 90; "
    "[console]::Beep(1600, 500)"
)
_AGGRESSIVE_FLOW_SCRIPT = (
    "[console]::Beep(1350, 120); Start-Sleep -Milliseconds 60; [console]::Beep(1650, 220)"
)
_EARLY_INTRADAY_SCRIPT = (
    "[console]::Beep(720, 140); Start-Sleep -Milliseconds 70; [console]::Beep(980, 240)"
)
_ENTRY_ZONE_WATCH_SCRIPT = "[console]::Beep(820, 260)"
_SWING_SETUP_WATCH_SCRIPT = "[console]::Beep(660, 180)"
_ENTRY_CLOSE_SCRIPT = (
    "[console]::Beep(1100, 180); Start-Sleep -Milliseconds 70; "
    "[console]::Beep(750, 240); Start-Sleep -Milliseconds 70; [console]::Beep(450, 380)"
)
_BUY_MATURITY_SCRIPTS = {
    BuyMaturity.TACTICAL_RECOVERY: "[console]::Beep(780, 260)",
    BuyMaturity.SWING_CONFIRMED: (
        "[console]::Beep(850, 180); Start-Sleep -Milliseconds 90; [console]::Beep(1150, 320)"
    ),
    BuyMaturity.HIGH_CONVICTION: (
        "[console]::Beep(1000, 160); Start-Sleep -Milliseconds 70; "
        "[console]::Beep(1250, 190); Start-Sleep -Milliseconds 70; "
        "[console]::Beep(1550, 380)"
    ),
    BuyMaturity.FULLY_MATURED: _SOLID_BUY_SCRIPT,
}


def play_patreon_confirmation_sound(*, fallback: TextIO) -> bool:
    """Launch the Patreon three-tone Windows chime or emit a terminal bell."""

    return _play_windows_sound(_PATREON_CONFIRMATION_SCRIPT, fallback=fallback)


def play_solid_buy_sound(*, fallback: TextIO) -> bool:
    """Play the strong alarm shared by final non-core family confirmations."""

    return _play_windows_sound(_SOLID_BUY_SCRIPT, fallback=fallback)


def play_buy_maturity_sound(maturity: BuyMaturity, *, fallback: TextIO) -> bool:
    """Play the distinct native pattern assigned to one buy maturity."""

    return _play_windows_sound(_BUY_MATURITY_SCRIPTS[maturity], fallback=fallback)


def play_aggressive_flow_sound(*, fallback: TextIO) -> bool:
    """Play the short two-tone alarm for an aggressive buy-pressure watch."""

    return _play_windows_sound(_AGGRESSIVE_FLOW_SCRIPT, fallback=fallback)


def play_early_intraday_sound(*, fallback: TextIO) -> bool:
    """Play a short rising watch tone distinct from confirmed-buy alarms."""

    return _play_windows_sound(_EARLY_INTRADAY_SCRIPT, fallback=fallback)


def play_entry_zone_watch_sound(*, fallback: TextIO) -> bool:
    """Play one medium watch tone for in-zone and breakaway candidates."""

    return _play_windows_sound(_ENTRY_ZONE_WATCH_SCRIPT, fallback=fallback)


def play_swing_setup_watch_sound(*, fallback: TextIO) -> bool:
    """Play one soft tone for an actionable Swing setup awaiting timing."""

    return _play_windows_sound(_SWING_SETUP_WATCH_SCRIPT, fallback=fallback)


def play_entry_close_sound(*, fallback: TextIO) -> bool:
    """Play a descending alarm for invalidation or audited paper-trade closure."""

    return _play_windows_sound(_ENTRY_CLOSE_SCRIPT, fallback=fallback)


def _play_windows_sound(script: str, *, fallback: TextIO) -> bool:
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
                script,
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
