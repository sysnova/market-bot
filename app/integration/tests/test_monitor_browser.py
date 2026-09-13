import subprocess
from unittest.mock import patch

import pytest


def test_wsl_opens_windows_browser_without_launching_a_linux_text_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.integration.monitor_browser import open_monitor_browser

    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    with (
        patch(
            "app.integration.monitor_browser.shutil.which", return_value="/windows/powershell.exe"
        ),
        patch("app.integration.monitor_browser.subprocess.run") as run,
        patch("app.integration.monitor_browser.webbrowser.open") as native,
    ):
        run.return_value = subprocess.CompletedProcess([], 0)
        assert open_monitor_browser("http://127.0.0.1:8765/") is True
        assert run.call_args.args[0][-1] == "Start-Process -FilePath 'http://127.0.0.1:8765/'"
        assert run.call_args.kwargs["timeout"] == 10
        native.assert_not_called()


def test_native_desktop_opens_default_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.integration.monitor_browser import open_monitor_browser

    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    monkeypatch.delenv("WSL_INTEROP", raising=False)
    with patch("app.integration.monitor_browser.webbrowser.open", return_value=True) as native:
        assert open_monitor_browser("http://127.0.0.1:8765/") is True
        native.assert_called_once_with("http://127.0.0.1:8765/")


def test_browser_failure_does_not_stop_monitoring(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.integration.monitor_browser import open_monitor_browser

    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    with (
        patch(
            "app.integration.monitor_browser.shutil.which", return_value="/windows/powershell.exe"
        ),
        patch("app.integration.monitor_browser.subprocess.run", side_effect=OSError("unavailable")),
    ):
        assert open_monitor_browser("http://127.0.0.1:8765/") is False


def test_browser_rejects_nonlocal_or_shell_input() -> None:
    from app.integration.monitor_browser import open_monitor_browser

    with pytest.raises(ValueError):
        open_monitor_browser("http://127.0.0.1:8765/'; Write-Output surprise")
