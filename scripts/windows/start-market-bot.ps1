# Starts MarketBot from any PowerShell working directory.
[CmdletBinding()]
param(
    [string]$Symbols,
    [switch]$Once,
    [switch]$NoNats,
    [switch]$NoBell,
    [string]$RuntimeRoot = ".runtime",
    [ValidateRange(30, 1800)]
    [int]$ReadyTimeoutSeconds = 600,
    [switch]$DryRun,
    [switch]$NoTileWindows
)

$ErrorActionPreference = "Stop"

$ProjectRoot = [System.IO.Path]::GetFullPath(
    (Join-Path -Path $PSScriptRoot -ChildPath "..\..")
)
. (Join-Path -Path $PSScriptRoot -ChildPath "environment.ps1")
$WindowsEnvironment = Set-MarketBotWindowsEnvironment -ProjectRoot $ProjectRoot

if ([System.IO.Path]::IsPathRooted($RuntimeRoot)) {
    $ResolvedRuntimeRoot = [System.IO.Path]::GetFullPath($RuntimeRoot)
}
else {
    $ResolvedRuntimeRoot = [System.IO.Path]::GetFullPath(
        (Join-Path -Path $ProjectRoot -ChildPath $RuntimeRoot)
    )
}

$UvCommand = Get-Command uv -ErrorAction SilentlyContinue
if ($null -eq $UvCommand) {
    throw "uv is not installed or is not available in PATH. Install uv and reopen PowerShell."
}

# Preserve the original one-shot/no-NATS command for diagnostics and offline use.
if ($Once -or $NoNats) {
    $MarketBotArguments = [System.Collections.Generic.List[string]]::new()
    foreach ($Argument in @(
        "run",
        "marketbot",
        "live",
        "--runtime-root",
        $ResolvedRuntimeRoot
    )) {
        $MarketBotArguments.Add($Argument)
    }
    if ($Once) {
        $MarketBotArguments.Add("--once")
    }
    if ($NoNats) {
        $MarketBotArguments.Add("--no-nats")
    }
    if ($NoBell) {
        $MarketBotArguments.Add("--no-bell")
    }
    if (-not [string]::IsNullOrWhiteSpace($Symbols)) {
        $MarketBotArguments.Add("--symbols")
        $MarketBotArguments.Add($Symbols)
    }
    if ($DryRun) {
        [pscustomobject]@{
            executable = $UvCommand.Source
            environment = $WindowsEnvironment
            arguments = $MarketBotArguments.ToArray()
            working_directory = $ProjectRoot
        } | ConvertTo-Json -Depth 3
        exit 0
    }
    Push-Location $ProjectRoot
    try {
        & $UvCommand.Source @MarketBotArguments
        $MarketBotExitCode = $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
    exit $MarketBotExitCode
}

$StatusRoot = Join-Path -Path $ResolvedRuntimeRoot -ChildPath "status"
$LogRoot = Join-Path -Path $ResolvedRuntimeRoot -ChildPath "logs"
$ReadyFiles = [ordered]@{
    "outbox-relay" = Join-Path -Path $StatusRoot -ChildPath "outbox-relay.ready.json"
    "alert" = Join-Path -Path $StatusRoot -ChildPath "alert.ready.json"
    "entry-watcher" = Join-Path -Path $StatusRoot -ChildPath "entry-watcher.ready.json"
    "entry-opportunity" = Join-Path -Path $StatusRoot -ChildPath "entry-opportunity.ready.json"
    "entry-recovery" = Join-Path -Path $StatusRoot -ChildPath "entry-recovery.ready.json"
    "market-history-v1" = Join-Path -Path $StatusRoot -ChildPath "market-history-v1.ready.json"
    "long-term" = Join-Path -Path $StatusRoot -ChildPath "long-term.ready.json"
    "swing" = Join-Path -Path $StatusRoot -ChildPath "swing.ready.json"
    "intraday" = Join-Path -Path $StatusRoot -ChildPath "intraday.ready.json"
    "market-rotation-v1" = Join-Path -Path $StatusRoot -ChildPath "market-rotation-v1.ready.json"
    "portfolio-flow-v1" = Join-Path -Path $StatusRoot -ChildPath "portfolio-flow-v1.ready.json"
    "long-portfolio-v1" = Join-Path -Path $StatusRoot -ChildPath "long-portfolio-v1.ready.json"
    "patreon-caps-v1" = Join-Path -Path $StatusRoot -ChildPath "patreon-caps-v1.ready.json"
    "confirmed-buy-monitor" = Join-Path -Path $StatusRoot -ChildPath "confirmed-buy-monitor.ready.json"
    "long-portfolio-monitor" = Join-Path -Path $StatusRoot -ChildPath "long-portfolio-monitor.ready.json"
}

function New-ProcessSpec {
    param(
        [string]$Name,
        [string[]]$Arguments
    )
    [pscustomobject]@{
        name = $Name
        arguments = $Arguments
    }
}

function Add-SymbolArguments {
    param([System.Collections.Generic.List[string]]$Arguments)
    if (-not [string]::IsNullOrWhiteSpace($Symbols)) {
        $Arguments.Add("--symbols")
        $Arguments.Add($Symbols)
    }
}

$ProcessSpecs = [System.Collections.Generic.List[object]]::new()
$AlertArguments = [System.Collections.Generic.List[string]]::new()
foreach ($Argument in @(
    "run",
    "marketbot",
    "alerts",
    "serve",
    "--runtime-root",
    $ResolvedRuntimeRoot,
    "--ready-path",
    $ReadyFiles["alert"]
)) {
    $AlertArguments.Add($Argument)
}
if ($NoBell) {
    $AlertArguments.Add("--no-bell")
}
$ProcessSpecs.Add((New-ProcessSpec -Name "alert" -Arguments $AlertArguments.ToArray()))

$EntryWatchArguments = @(
    "run",
    "marketbot",
    "entry-watch",
    "serve",
    "--ready-path",
    $ReadyFiles["entry-watcher"]
)
$ProcessSpecs.Add((New-ProcessSpec -Name "entry-watcher" -Arguments $EntryWatchArguments))

$EntryOpportunityArguments = @(
    "run",
    "marketbot",
    "entry-opportunity",
    "serve",
    "--ready-path",
    $ReadyFiles["entry-opportunity"]
)
$ProcessSpecs.Add((New-ProcessSpec -Name "entry-opportunity" -Arguments $EntryOpportunityArguments))

$MarketHistoryArguments = @(
    "run", "marketbot", "market", "history",
    "--ready-path", $ReadyFiles["market-history-v1"]
)
$ProcessSpecs.Add((New-ProcessSpec -Name "market-history-v1" -Arguments $MarketHistoryArguments))

foreach ($Definition in @(
    [pscustomobject]@{ Name = "long-term"; Command = "long" },
    [pscustomobject]@{ Name = "swing"; Command = "swing" },
    [pscustomobject]@{ Name = "intraday"; Command = "intraday" }
)) {
    $Arguments = [System.Collections.Generic.List[string]]::new()
    foreach ($Argument in @(
        "run",
        "marketbot",
        "engine",
        $Definition.Command,
        "--ready-path",
        $ReadyFiles[$Definition.Name]
    )) {
        $Arguments.Add($Argument)
    }
    Add-SymbolArguments -Arguments $Arguments
    $ProcessSpecs.Add((New-ProcessSpec -Name $Definition.Name -Arguments $Arguments.ToArray()))
}

$RotationArguments = @(
    "run", "marketbot", "engine", "rotation",
    "--ready-path", $ReadyFiles["market-rotation-v1"]
)
$ProcessSpecs.Add((New-ProcessSpec -Name "market-rotation-v1" -Arguments $RotationArguments))

$PortfolioFlowArguments = @(
    "run", "marketbot", "engine", "portfolio-flow",
    "--ready-path", $ReadyFiles["portfolio-flow-v1"]
)
$ProcessSpecs.Add((New-ProcessSpec -Name "portfolio-flow-v1" -Arguments $PortfolioFlowArguments))

$LongPortfolioArguments = @(
    "run", "marketbot", "engine", "long-portfolio",
    "--runtime-root", $ResolvedRuntimeRoot,
    "--ready-path", $ReadyFiles["long-portfolio-v1"]
)
$ProcessSpecs.Add((New-ProcessSpec -Name "long-portfolio-v1" -Arguments $LongPortfolioArguments))

$PatreonCapsArguments = @(
    "run", "marketbot", "engine", "patreon-caps",
    "--ready-path", $ReadyFiles["patreon-caps-v1"]
)
$ProcessSpecs.Add((New-ProcessSpec -Name "patreon-caps-v1" -Arguments $PatreonCapsArguments))

$ConfirmedBuyArguments = [System.Collections.Generic.List[string]]::new()
foreach ($Argument in @(
    "run", "marketbot", "alerts", "confirmed",
    "--ready-path", $ReadyFiles["confirmed-buy-monitor"]
)) {
    $ConfirmedBuyArguments.Add($Argument)
}
if ($NoBell) {
    $ConfirmedBuyArguments.Add("--no-bell")
}
$ProcessSpecs.Add((New-ProcessSpec -Name "confirmed-buy-monitor" -Arguments $ConfirmedBuyArguments.ToArray()))

$StreamArguments = [System.Collections.Generic.List[string]]::new()
foreach ($Argument in @("run", "marketbot", "market", "stream")) {
    $StreamArguments.Add($Argument)
}
Add-SymbolArguments -Arguments $StreamArguments
$ProcessSpecs.Add((New-ProcessSpec -Name "alpaca-market-stream" -Arguments $StreamArguments.ToArray()))

$OutboxArguments = @(
    "run", "marketbot", "outbox", "serve",
    "--ready-path", $ReadyFiles["outbox-relay"]
)
$ProcessSpecs.Add((New-ProcessSpec -Name "outbox-relay" -Arguments $OutboxArguments))

$EntryRecoveryArguments = @(
    "run", "marketbot", "engine", "entry-recovery",
    "--ready-path", $ReadyFiles["entry-recovery"]
)
$ProcessSpecs.Add((New-ProcessSpec -Name "entry-recovery" -Arguments $EntryRecoveryArguments))

if ($DryRun) {
    [pscustomobject]@{
        mode = "distributed"
        executable = $UvCommand.Source
        environment = $WindowsEnvironment
        working_directory = $ProjectRoot
        processes = $ProcessSpecs.ToArray()
    } | ConvertTo-Json -Depth 5
    exit 0
}

New-Item -ItemType Directory -Path $StatusRoot -Force | Out-Null
New-Item -ItemType Directory -Path $LogRoot -Force | Out-Null
foreach ($ReadyFile in $ReadyFiles.Values) {
    if (Test-Path -LiteralPath $ReadyFile) {
        Remove-Item -LiteralPath $ReadyFile -Force
    }
}

function Start-MarketBotProcess {
    param([object]$Spec)
    $StdoutPath = Join-Path -Path $LogRoot -ChildPath "$($Spec.name).out.log"
    $StderrPath = Join-Path -Path $LogRoot -ChildPath "$($Spec.name).err.log"
    $ArgumentLine = ($Spec.arguments | ForEach-Object {
        '"' + ([string]$_).Replace('"', '\"') + '"'
    }) -join " "
    $Visible = $Spec.name -in @("alert", "confirmed-buy-monitor")
    if ($Visible) {
        $WindowsTerminal = Get-Command wt.exe -ErrorAction Stop
        $Role = if ($Spec.name -eq "alert") {
            "analysis"
        }
        else {
            "confirmed"
        }
        $ArgumentsPath = Join-Path -Path $StatusRoot -ChildPath "$($Spec.name).arguments.json"
        [System.IO.File]::WriteAllText(
            $ArgumentsPath,
            ($Spec.arguments | ConvertTo-Json -Compress)
        )
        $PidPath = Join-Path -Path $StatusRoot -ChildPath "$($Spec.name).host.pid"
        Remove-Item -LiteralPath $PidPath -Force -ErrorAction SilentlyContinue
        $VisibleHost = Join-Path -Path $PSScriptRoot -ChildPath "run-visible-marketbot.ps1"
        $Title = if ($Role -eq "analysis") { "MarketBot Analysis" } else { "MarketBot Confirmed Buys" }
        $TerminalArguments = @(
            "-w", "-1", "new-tab", "--title", $Title,
            "--suppressApplicationTitle", "--startingDirectory", $ProjectRoot,
            "powershell.exe", "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", $VisibleHost, "-Role", $Role, "-PidPath", $PidPath,
            "-Executable", $UvCommand.Source, "-ArgumentsPath", $ArgumentsPath
        )
        $TerminalArgumentLine = ($TerminalArguments | ForEach-Object {
            '"' + ([string]$_).Replace('"', '\"') + '"'
        }) -join " "
        Start-Process -FilePath $WindowsTerminal.Source -ArgumentList $TerminalArgumentLine | Out-Null
        $Deadline = [DateTime]::UtcNow.AddSeconds(10)
        while (-not (Test-Path -LiteralPath $PidPath)) {
            if ([DateTime]::UtcNow -ge $Deadline) {
                throw "$Title did not expose its host PID."
            }
            Start-Sleep -Milliseconds 100
        }
        $VisibleProcess = Get-Process -Id ([int](Get-Content -LiteralPath $PidPath -Raw))
        return [pscustomobject]@{
            name = $Spec.name
            process = $VisibleProcess
            stdout = "visible Windows Terminal window"
            stderr = "visible Windows Terminal window"
        }
    }
    $StartParameters = @{
        FilePath = $UvCommand.Source
        ArgumentList = $ArgumentLine
        WorkingDirectory = $ProjectRoot
        PassThru = $true
    }
    $StartParameters["WindowStyle"] = "Hidden"
    $StartParameters["RedirectStandardOutput"] = $StdoutPath
    $StartParameters["RedirectStandardError"] = $StderrPath
    $Process = Start-Process @StartParameters
    [pscustomobject]@{
        name = $Spec.name
        process = $Process
        stdout = $StdoutPath
        stderr = $StderrPath
    }
}

function Wait-MarketBotReadiness {
    param([string[]]$Paths)
    $Deadline = [DateTime]::UtcNow.AddSeconds($ReadyTimeoutSeconds)
    while ($true) {
        $Missing = @($Paths | Where-Object { -not (Test-Path -LiteralPath $_) })
        if ($Missing.Count -eq 0) {
            return
        }
        foreach ($Child in $Children) {
            if ($Child.process.HasExited) {
                throw "$($Child.name) exited before readiness. Inspect $($Child.stderr)."
            }
        }
        if ([DateTime]::UtcNow -ge $Deadline) {
            throw "Timed out waiting for process readiness: $($Missing -join ', ')."
        }
        Start-Sleep -Milliseconds 500
    }
}

function Stop-MarketBotProcessTree {
    param([System.Diagnostics.Process]$Process)
    if ($Process.HasExited) {
        return
    }
    $TaskKill = Join-Path -Path $env:SystemRoot -ChildPath "System32\taskkill.exe"
    if (Test-Path -LiteralPath $TaskKill) {
        & $TaskKill @("/PID", [string]$Process.Id, "/T", "/F") 2>$null | Out-Null
    }
    else {
        Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
    }
    Wait-Process -Id $Process.Id -Timeout 5 -ErrorAction SilentlyContinue
}

function Initialize-WindowLayoutApi {
    if ("MarketBotWindowLayoutV3.NativeMethods" -as [type]) {
        return
    }
    Add-Type -TypeDefinition @"
using System;
using System.Text;
using System.Runtime.InteropServices;

namespace MarketBotWindowLayoutV3 {
    public static class NativeMethods {
        public delegate bool EnumWindowsCallback(IntPtr window, IntPtr parameter);

        [DllImport("user32.dll")]
        private static extern bool EnumWindows(EnumWindowsCallback callback, IntPtr parameter);

        [DllImport("user32.dll")]
        private static extern bool IsWindowVisible(IntPtr window);

        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        private static extern int GetWindowText(IntPtr window, StringBuilder text, int count);

        public static IntPtr FindVisibleWindow(string title) {
            IntPtr match = IntPtr.Zero;
            EnumWindows((window, parameter) => {
                if (!IsWindowVisible(window)) return true;
                var text = new StringBuilder(512);
                GetWindowText(window, text, text.Capacity);
                if (text.ToString().Contains(title)) {
                    match = window;
                    return false;
                }
                return true;
            }, IntPtr.Zero);
            return match;
        }

        [DllImport("user32.dll", SetLastError = true)]
        public static extern bool MoveWindow(
            IntPtr hWnd, int X, int Y, int width, int height, bool repaint
        );

        [DllImport("user32.dll")]
        public static extern bool ShowWindow(IntPtr hWnd, int command);

        [DllImport("user32.dll")]
        public static extern bool PostMessage(
            IntPtr hWnd, uint message, IntPtr wParam, IntPtr lParam
        );
    }
}
"@
}

function Get-MarketBotWindowHandle {
    param(
        [System.Diagnostics.Process]$Process,
        [string]$Title,
        [ValidateRange(1, 30)][int]$TimeoutSeconds = 2
    )
    $Deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $Deadline) {
        $Process.Refresh()
        $Handle = $Process.MainWindowHandle
        if ($Handle -eq [IntPtr]::Zero -and -not [string]::IsNullOrWhiteSpace($Title)) {
            $Handle = [MarketBotWindowLayoutV3.NativeMethods]::FindVisibleWindow($Title)
        }
        if ($Handle -ne [IntPtr]::Zero) {
            return $Handle
        }
        Start-Sleep -Milliseconds 200
    }
    return [IntPtr]::Zero
}

function Set-MarketBotVerticalWindowLayout {
    param(
        [System.Diagnostics.Process]$AnalysisProcess,
        [System.Diagnostics.Process]$ConfirmedBuyProcess
    )
    try {
        Initialize-WindowLayoutApi
        Add-Type -AssemblyName System.Windows.Forms
        $WorkingArea = [System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea
        $CurrentProcess = Get-Process -Id $PID
        $MainHandle = Get-MarketBotWindowHandle $CurrentProcess "MarketBot Control"
        $AnalysisHandle = Get-MarketBotWindowHandle $AnalysisProcess "MarketBot Analysis"
        $ConfirmedHandle = Get-MarketBotWindowHandle $ConfirmedBuyProcess "MarketBot Confirmed Buys"
        $script:AnalysisWindowHandle = $AnalysisHandle
        $script:ConfirmedBuyWindowHandle = $ConfirmedHandle
        $Handles = @($MainHandle, $AnalysisHandle, $ConfirmedHandle)
        if ($Handles | Where-Object { $_ -eq [IntPtr]::Zero }) {
            Write-Warning "Could not detect all console windows; automatic mosaic was skipped."
            return
        }
        if ($NoTileWindows) {
            return
        }
        $RowHeight = [int][Math]::Floor($WorkingArea.Height / 3)
        for ($Index = 0; $Index -lt $Handles.Count; $Index++) {
            $Top = [int]($WorkingArea.Top + ($RowHeight * $Index))
            $Height = if ($Index -eq 2) {
                [int]($WorkingArea.Bottom - $Top)
            }
            else {
                $RowHeight
            }
            [MarketBotWindowLayoutV3.NativeMethods]::ShowWindow($Handles[$Index], 9) | Out-Null
            $Moved = [MarketBotWindowLayoutV3.NativeMethods]::MoveWindow(
                $Handles[$Index], [int]$WorkingArea.Left, $Top,
                [int]$WorkingArea.Width, $Height, $true
            )
            if (-not $Moved) {
                Write-Warning "Windows rejected one MarketBot window position."
            }
        }
    }
    catch {
        Write-Warning "Automatic mosaic failed but MarketBot will continue: $($_.Exception.Message)"
    }
}

function Close-MarketBotMonitorWindows {
    Initialize-WindowLayoutApi
    foreach ($Handle in @($script:AnalysisWindowHandle, $script:ConfirmedBuyWindowHandle)) {
        if ($null -ne $Handle -and $Handle -ne [IntPtr]::Zero) {
            [MarketBotWindowLayoutV3.NativeMethods]::PostMessage(
                $Handle, 0x0010, [IntPtr]::Zero, [IntPtr]::Zero
            ) | Out-Null
        }
    }
}

$Children = [System.Collections.Generic.List[object]]::new()
try {
    $Host.UI.RawUI.WindowTitle = "MarketBot Control"
    Write-Host "Starting independent MarketBot processes..." -ForegroundColor Cyan
    Write-Host "Project: $ProjectRoot"
    Write-Host "Runtime: $ResolvedRuntimeRoot"
    $OutboxChild = Start-MarketBotProcess -Spec $ProcessSpecs[13]
    $Children.Add($OutboxChild)
    Write-Host "Started outbox-relay (PID $($OutboxChild.process.Id))"
    Wait-MarketBotReadiness -Paths @($ReadyFiles["outbox-relay"])
    $RecoveryChild = Start-MarketBotProcess -Spec $ProcessSpecs[14]
    $Children.Add($RecoveryChild)
    Write-Host "Started entry-recovery (PID $($RecoveryChild.process.Id))"
    Wait-MarketBotReadiness -Paths @($ReadyFiles["entry-recovery"])
    for ($Index = 0; $Index -lt 3; $Index++) {
        $Child = Start-MarketBotProcess -Spec $ProcessSpecs[$Index]
        $Children.Add($Child)
        Write-Host "Started $($Child.name) (PID $($Child.process.Id))"
    }
    Wait-MarketBotReadiness -Paths @(
        $ReadyFiles["alert"],
        $ReadyFiles["entry-watcher"],
        $ReadyFiles["entry-opportunity"]
    )

    $HistoryChild = Start-MarketBotProcess -Spec $ProcessSpecs[3]
    $Children.Add($HistoryChild)
    Write-Host "Started market-history-v1 (PID $($HistoryChild.process.Id))"
    Wait-MarketBotReadiness -Paths @($ReadyFiles["market-history-v1"])

    $ConfirmedChild = Start-MarketBotProcess -Spec $ProcessSpecs[11]
    $Children.Add($ConfirmedChild)
    Write-Host "Started confirmed-buy monitor (PID $($ConfirmedChild.process.Id))"
    Wait-MarketBotReadiness -Paths @($ReadyFiles["confirmed-buy-monitor"])
    $AlertChild = $Children | Where-Object { $_.name -eq "alert" } | Select-Object -First 1
    Set-MarketBotVerticalWindowLayout `
        -AnalysisProcess $AlertChild.process `
        -ConfirmedBuyProcess $ConfirmedChild.process

    for ($Index = 4; $Index -lt 11; $Index++) {
        $Child = Start-MarketBotProcess -Spec $ProcessSpecs[$Index]
        $Children.Add($Child)
        Write-Host "Started $($Child.name) (PID $($Child.process.Id))"
    }
    Wait-MarketBotReadiness -Paths @(
        $ReadyFiles["long-term"],
        $ReadyFiles["swing"],
        $ReadyFiles["intraday"],
        $ReadyFiles["market-rotation-v1"],
        $ReadyFiles["portfolio-flow-v1"],
        $ReadyFiles["long-portfolio-v1"],
        $ReadyFiles["patreon-caps-v1"]
    )

    $StreamChild = Start-MarketBotProcess -Spec $ProcessSpecs[12]
    $Children.Add($StreamChild)
    $TmuxLauncher = Join-Path $PSScriptRoot "start-long-portfolio-tmux.ps1"
    $TmuxArguments = @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $TmuxLauncher,
        "-ReadyPath", $ReadyFiles["long-portfolio-monitor"]
    )
    if ($NoBell) {
        $TmuxArguments += "-NoBell"
    }
    & powershell.exe @TmuxArguments
    Wait-MarketBotReadiness -Paths @($ReadyFiles["long-portfolio-monitor"])
    Write-Host "All engines ready. Started Alpaca WebSocket (PID $($StreamChild.process.Id))."
    Write-Host "Logs: $LogRoot"
    Write-Host "Press Ctrl+C to stop every process."

    while ($true) {
        foreach ($Child in $Children) {
            if ($Child.process.HasExited) {
                throw "$($Child.name) exited unexpectedly. Inspect $($Child.stderr)."
            }
        }
        Start-Sleep -Seconds 1
    }
}
finally {
    foreach ($Child in $Children) {
        Stop-MarketBotProcessTree -Process $Child.process
    }
    Close-MarketBotMonitorWindows
    & wsl.exe sh -lc "tmux kill-session -t marketbot-long 2>/dev/null || true"
    Get-ChildItem -LiteralPath $StatusRoot -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '\.(host\.pid|arguments\.json)$' } |
        Remove-Item -Force -ErrorAction SilentlyContinue
}
