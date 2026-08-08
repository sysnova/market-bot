import json
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parents[3]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "windows" / "start-market-bot.ps1"
BOOTSTRAP_SCRIPT_PATH = PROJECT_ROOT / "scripts" / "windows" / "setup-market-bot.ps1"
SEC_SCRIPT_PATH = PROJECT_ROOT / "scripts" / "windows" / "run-sec-bot.ps1"
JETSTREAM_MONITOR_SCRIPT_PATH = PROJECT_ROOT / "scripts" / "windows" / "watch-jetstream.ps1"
LONG_TMUX_SCRIPT_PATH = PROJECT_ROOT / "scripts" / "windows" / "start-long-portfolio-tmux.ps1"
VISIBLE_HOST_SCRIPT_PATH = PROJECT_ROOT / "scripts" / "windows" / "run-visible-marketbot.ps1"
WIREGUARD_HOST_SCRIPT_PATH = (
    PROJECT_ROOT / "scripts" / "windows" / "configure-wireguard-host.ps1"
)
WIREGUARD_CLIENT_SCRIPT_PATH = (
    PROJECT_ROOT / "scripts" / "windows" / "configure-wireguard-client.ps1"
)
WIREGUARD_FIREWALL_SCRIPT_PATH = (
    PROJECT_ROOT / "scripts" / "windows" / "configure-wireguard-firewall.ps1"
)
NATS_WIREGUARD_SCRIPT_PATH = (
    PROJECT_ROOT / "scripts" / "windows" / "recreate-nats-for-wireguard.ps1"
)
POWERSHELL = shutil.which("powershell")


def test_windows_launcher_stops_complete_child_process_trees() -> None:
    script = SCRIPT_PATH.read_text(encoding="utf-8")

    assert "function Stop-MarketBotProcessTree" in script
    assert '"/PID", [string]$Process.Id, "/T", "/F"' in script
    assert "Stop-MarketBotProcessTree -Process $Child.process" in script
    assert "function Close-MarketBotMonitorWindows" in script
    assert "::PostMessage(" in script
    assert "Close-MarketBotMonitorWindows" in script


def test_windows_launcher_tiles_three_visible_windows_vertically() -> None:
    script = SCRIPT_PATH.read_text(encoding="utf-8")

    assert "function Set-MarketBotVerticalWindowLayout" in script
    assert "$WorkingArea.Height / 3" in script
    assert "MarketBotWindowLayoutV3.NativeMethods]::FindVisibleWindow($Title)" in script
    assert '"MarketBot Control"' in script
    assert '"MarketBot Analysis"' in script
    assert '"MarketBot Confirmed Buys"' in script
    assert '"-w", "-1"' in script
    assert "$Process.MainWindowHandle" in script
    assert '"-PidPath", $PidPath' in script
    assert "MoveWindow" in script
    assert "Automatic mosaic failed but MarketBot will continue" in script


def test_visible_windows_reselect_the_native_environment() -> None:
    script = VISIBLE_HOST_SCRIPT_PATH.read_text(encoding="utf-8")

    assert '"environment.ps1"' in script
    assert "Set-MarketBotWindowsEnvironment -ProjectRoot $ProjectRoot" in script


def test_nats_wireguard_replacement_preserves_volume_and_requires_apply() -> None:
    script = NATS_WIREGUARD_SCRIPT_PATH.read_text(encoding="utf-8")

    assert "[switch]$Apply" in script
    assert "marketbot_nats-data" in script
    assert "docker rm $ContainerName" in script
    assert "docker volume rm" not in script
    assert "127.0.0.1:4222:4222" in script
    assert '${WireGuardHostIp}:4222:4222' in script
    assert "127.0.0.1:8222:8222" in script
    assert "'--restart', 'unless-stopped'" in script


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
def test_wireguard_host_script_has_non_mutating_dry_run() -> None:
    result = subprocess.run(  # noqa: S603 - executable is resolved from PATH for this test.
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(WIREGUARD_HOST_SCRIPT_PATH),
            "-ClientPublicKey",
            "A" * 43 + "=",
            "-DryRun",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    plan = json.loads(result.stdout)
    assert plan["host_address"] == "10.77.77.1/24"
    assert plan["client_allowed_ip"] == "10.77.77.2/32"
    assert plan["listen_udp"] == 51820
    assert plan["nats_endpoint"] == "nats://10.77.77.1:4222"
    assert plan["activation"] == "manual"
    assert plan["installs_tunnel_service"] is False


def test_wireguard_host_tunnel_is_manually_activated() -> None:
    script = WIREGUARD_HOST_SCRIPT_PATH.read_text(encoding="utf-8")

    assert "/installtunnelservice" not in script
    assert "Start-Service" not in script
    assert 'activation = "manual"' in script


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
def test_wireguard_client_dry_run_describes_key_exchange_without_mutating() -> None:
    result = subprocess.run(  # noqa: S603 - executable is resolved from PATH for this test.
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(WIREGUARD_CLIENT_SCRIPT_PATH),
            "-DryRun",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    plan = json.loads(result.stdout)
    assert plan["stage"] == "generate-key"
    assert plan["client_address"] == "10.77.77.2/24"
    assert plan["host_allowed_ip"] == "10.77.77.1/32"
    assert plan["activation"] == "manual"
    assert plan["installs_tunnel_service"] is False


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
def test_wireguard_client_dry_run_describes_completed_config() -> None:
    result = subprocess.run(  # noqa: S603 - executable is resolved from PATH for this test.
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(WIREGUARD_CLIENT_SCRIPT_PATH),
            "-HostPublicKey",
            "A" * 43 + "=",
            "-Endpoint",
            "vpn.example.com:51820",
            "-DryRun",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    plan = json.loads(result.stdout)
    assert plan["stage"] == "complete-config"
    assert plan["endpoint"] == "vpn.example.com:51820"
    assert plan["persistent_keepalive"] == 25


def test_wireguard_client_never_installs_or_starts_a_tunnel_service() -> None:
    script = WIREGUARD_CLIENT_SCRIPT_PATH.read_text(encoding="utf-8")

    assert "/installtunnelservice" not in script
    assert "Start-Service" not in script
    assert 'private_key_shared = $false' in script


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
def test_wireguard_host_can_prepare_keys_without_admin(tmp_path: Path) -> None:
    result = subprocess.run(  # noqa: S603 - executable is resolved from PATH for this test.
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(WIREGUARD_HOST_SCRIPT_PATH),
            "-ClientPublicKey",
            "A" * 43 + "=",
            "-ConfigRoot",
            str(tmp_path),
            "-PrepareOnly",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    prepared = json.loads(result.stdout)
    assert prepared["status"] == "prepared-without-firewall"
    assert prepared["firewall_configured"] is False
    assert prepared["host_public_key"]
    assert (tmp_path / "marketbot.conf").is_file()


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
def test_wireguard_firewall_script_has_non_mutating_dry_run() -> None:
    result = subprocess.run(  # noqa: S603 - executable is resolved from PATH for this test.
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(WIREGUARD_FIREWALL_SCRIPT_PATH),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    plan = json.loads(result.stdout)
    assert plan["mode"] == "dry-run"
    assert plan["action"] == "ensure"
    assert plan["protocol"] == "UDP"
    assert plan["public_port"] == 51820
    assert plan["private_port"] == 51820


def test_wireguard_firewall_script_only_manages_udp_endpoint() -> None:
    script = WIREGUARD_FIREWALL_SCRIPT_PATH.read_text(encoding="utf-8")

    assert "New-NetFirewallRule" in script
    assert "Remove-NetFirewallRule" in script
    assert "-Protocol UDP" in script
    assert "4222" not in script
    assert "docker" not in script.lower()
    assert "winget" not in script.lower()


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
def test_windows_bootstrap_uses_a_platform_specific_environment() -> None:
    result = subprocess.run(  # noqa: S603 - executable is resolved from PATH for this test.
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(BOOTSTRAP_SCRIPT_PATH),
            "-DryRun",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    plan = json.loads(result.stdout)
    assert plan["environment"].endswith(".venv-windows")
    assert plan["python"] == "3.14"
    assert plan["sync_arguments"] == ["sync", "--locked"]


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
def test_jetstream_monitor_builds_subscription_without_connecting() -> None:
    result = subprocess.run(  # noqa: S603 - executable is resolved from PATH for this test.
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(JETSTREAM_MONITOR_SCRIPT_PATH),
            "-Subject",
            "marketbot.v1.analysis.result.>",
            "-DryRun",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    command = json.loads(result.stdout)
    assert command["subject"] == "marketbot.v1.analysis.result.>"
    assert command["nats_url"] == "nats://127.0.0.1:4222"
    assert command["executable"].endswith(".venv-windows\\Scripts\\python.exe")


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
    assert command["environment"].endswith(".venv-windows")
    assert command["arguments"][:3] == ["run", "marketbot", "live"]
    assert "--once" in command["arguments"]
    assert "--no-nats" in command["arguments"]
    assert "--no-bell" in command["arguments"]
    assert command["arguments"][-2:] == ["--symbols", "HIMS,ZETA"]


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
def test_windows_launcher_defaults_to_independent_processes() -> None:
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
            "-NoBell",
            "-DryRun",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    plan = json.loads(result.stdout)
    assert plan["mode"] == "distributed"
    assert plan["environment"].endswith(".venv-windows")
    assert [process["name"] for process in plan["processes"]] == [
        "alerts-v2",
        "entry-watcher-v5",
        "entry-opportunity-v1",
        "market-history-v1",
        "long-term-v2",
        "swing-v2",
        "intraday-v2",
        "market-rotation-v1",
        "portfolio-flow-v1",
        "long-portfolio-v1",
        "patreon-caps-v1",
        "confirmed-buy-monitor",
        "alpaca-market-stream",
    ]
    assert plan["processes"][2]["arguments"][:4] == [
        "run",
        "marketbot",
        "entry-opportunity",
        "serve",
    ]
    assert plan["processes"][3]["arguments"][:4] == [
        "run",
        "marketbot",
        "market",
        "history",
    ]
    assert plan["processes"][4]["arguments"][:4] == [
        "run",
        "marketbot",
        "engine",
        "long",
    ]
    assert plan["processes"][-1]["arguments"][:4] == [
        "run",
        "marketbot",
        "market",
        "stream",
    ]
    assert plan["processes"][7]["arguments"][:4] == [
        "run",
        "marketbot",
        "engine",
        "rotation",
    ]
    assert plan["processes"][8]["arguments"][:4] == [
        "run",
        "marketbot",
        "engine",
        "portfolio-flow",
    ]
    assert plan["processes"][9]["arguments"][:4] == [
        "run",
        "marketbot",
        "engine",
        "long-portfolio",
    ]
    assert plan["processes"][10]["arguments"][:4] == [
        "run",
        "marketbot",
        "engine",
        "patreon-caps",
    ]
    assert plan["processes"][11]["arguments"][:4] == [
        "run",
        "marketbot",
        "alerts",
        "confirmed",
    ]


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
def test_long_portfolio_tmux_launcher_builds_dedicated_session() -> None:
    result = subprocess.run(  # noqa: S603 - executable is resolved from PATH for this test.
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(LONG_TMUX_SCRIPT_PATH),
            "-NoBell",
            "-DryRun",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    command = json.loads(result.stdout)
    assert command["session"] == "marketbot-long"
    assert "run-long-portfolio-monitor.ps1" in command["pane_command"]
    assert "-NoBell" in command["pane_command"]


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
def test_windows_sec_launcher_builds_bounded_daily_command() -> None:
    result = subprocess.run(  # noqa: S603 - executable is resolved from PATH for this test.
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SEC_SCRIPT_PATH),
            "-Symbols",
            "HIMS,ZETA",
            "-LookbackDays",
            "90",
            "-NoNats",
            "-DryRun",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    command = json.loads(result.stdout)
    assert command["arguments"][:4] == ["run", "marketbot", "sec", "daily"]
    assert command["arguments"][-2:] == ["--symbols", "HIMS,ZETA"]
    assert "--lookback-days" in command["arguments"]
    assert "90" in command["arguments"]
    assert "--no-nats" in command["arguments"]
