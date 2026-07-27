import json
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parents[3]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "windows" / "start-market-bot.ps1"
POWERSHELL = shutil.which("powershell")


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
def test_windows_launcher_builds_live_command_without_running_it() -> None:
    result = subprocess.run(  # noqa: S603 - executable is resolved from PATH for this test.
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT_PATH),
            "-Symbols",
            "HIMS,ZETA",
            "-Once",
            "-NoNats",
            "-NoBell",
            "-DryRun",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    command = json.loads(result.stdout)
    assert command["working_directory"] == str(PROJECT_ROOT)
    assert command["executable"].endswith("uv.exe")
    assert command["arguments"][:3] == ["run", "marketbot", "live"]
    assert "--once" in command["arguments"]
    assert "--no-nats" in command["arguments"]
    assert "--no-bell" in command["arguments"]
    assert command["arguments"][-2:] == ["--symbols", "HIMS,ZETA"]
