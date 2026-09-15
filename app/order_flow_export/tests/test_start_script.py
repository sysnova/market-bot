import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Bash launcher runs on Linux/WSL")
SCRIPT = Path(__file__).resolve().parents[3] / "start-order-flow-websocket.sh"


def run_launcher(
    tmp_path: Path, *, configured: bool = False, busy: bool = False, probe_exit: int = 0,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    root = tmp_path / "project with spaces"
    python = root / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text(
        '#!/bin/bash\n'
        'case "$*" in\n'
        '  *"serve order-flow") printf "%s" "${MARKETBOT_ORDER_FLOW_WS_TOKEN:-dotenv}" '
        '> "$LAUNCH_CAPTURE"; exit 0 ;;\n'
        'esac\n'
        'printf "%s\\n" "$PROBE_RESULT"\n'
        'exit "$PROBE_EXIT"\n',
        encoding="utf-8",
    )
    python.chmod(0o755)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    ss = bin_dir / "ss"
    ss.write_text('#!/bin/sh\nprintf "%s" "$BUSY_OUTPUT"\n', encoding="utf-8")
    ss.chmod(0o755)
    capture = tmp_path / "launch-result"
    env = os.environ.copy()
    env.pop("MARKETBOT_ORDER_FLOW_WS_TOKEN", None)
    env.update(
        MARKETBOT_PROJECT_ROOT=str(root), LAUNCH_CAPTURE=str(capture),
        PROBE_RESULT=f"{'configured' if configured else 'missing'} 8766",
        PROBE_EXIT=str(probe_exit), BUSY_OUTPUT="LISTEN" if busy else "",
        PATH=str(bin_dir) + os.pathsep + env["PATH"],
    )
    result = subprocess.run(  # noqa: S603 -- Fixed repository script with controlled test inputs.
        ["/bin/bash", str(SCRIPT)], env=env, input="test-private-token\n",
        text=True, capture_output=True, timeout=5, check=False,
    )
    return result, capture


def test_prompts_for_missing_token_and_passes_it_only_via_environment(tmp_path: Path) -> None:
    result, capture = run_launcher(tmp_path)
    assert result.returncode == 0, result.stderr
    assert capture.read_text() == "test-private-token"
    assert "test-private-token" not in result.stdout + result.stderr


def test_preserves_configured_token_without_prompting(tmp_path: Path) -> None:
    result, capture = run_launcher(tmp_path, configured=True)
    assert result.returncode == 0, result.stderr
    assert capture.read_text() == "dotenv"


def test_occupied_port_does_not_launch_a_second_process(tmp_path: Path) -> None:
    result, capture = run_launcher(tmp_path, busy=True)
    assert result.returncode != 0
    assert "8766" in result.stderr and "ocupado" in result.stderr
    assert not capture.exists()


def test_bad_configuration_does_not_launch(tmp_path: Path) -> None:
    result, capture = run_launcher(tmp_path, probe_exit=2)
    assert result.returncode == 2
    assert not capture.exists()


def test_help_needs_no_environment() -> None:
    result = subprocess.run(  # noqa: S603 -- Fixed repository script and help argument.
        ["/bin/bash", str(SCRIPT), "--help"],
        capture_output=True, text=True, timeout=5, check=False,
    )
    assert result.returncode == 0
    assert "8766" in result.stdout
