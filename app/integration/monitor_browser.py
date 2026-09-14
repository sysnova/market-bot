"""Open a local monitor in the desktop browser, including Windows from WSL."""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import webbrowser
from pathlib import Path

_LOGGER = logging.getLogger(__name__)
_LOCAL_URL = re.compile(r"http://(?:127\.0\.0\.1|localhost|\[::1\]):[0-9]{1,5}/")


def open_monitor_browser(url: str) -> bool:
    """A browser failure must leave the monitoring server running."""
    if _LOCAL_URL.fullmatch(url) is None:
        raise ValueError("monitor browser requires a local HTTP origin")
    try:
        if os.environ.get("WSL_DISTRO_NAME") or os.environ.get("WSL_INTEROP"):
            powershell = shutil.which("powershell.exe")
            fallback = Path("/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")
            if powershell is None and fallback.is_file():
                powershell = str(fallback)
            if powershell is None:
                _LOGGER.warning("Windows browser launcher unavailable; open %s manually", url)
                return False
            result = subprocess.run(  # noqa: S603 - trusted executable and validated localhost URL.
                [
                    powershell,
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    f"Start-Process -FilePath '{url}'",
                ],
                # PowerShell must not read the parent launcher's pending startup batches.
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=10,
                check=False,
            )
            opened = result.returncode == 0
        else:
            opened = webbrowser.open(url)
    except (OSError, subprocess.SubprocessError, webbrowser.Error):
        _LOGGER.warning("Could not open the browser; monitoring remains available at %s", url)
        return False
    if not opened:
        _LOGGER.warning("Browser did not open; monitoring remains available at %s", url)
    return opened
