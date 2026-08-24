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
        "python",
        "-m",
        "app.operator_cli",
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
$PlanArguments = [System.Collections.Generic.List[string]]::new()
foreach ($Argument in @(
    "run", "python", "-m", "app.operator_cli", "runtime-plan", "--runtime-root", $ResolvedRuntimeRoot
)) {
    $PlanArguments.Add($Argument)
}
if ($NoBell) {
    $PlanArguments.Add("--no-bell")
}
if (-not [string]::IsNullOrWhiteSpace($Symbols)) {
    $PlanArguments.Add("--symbols")
    $PlanArguments.Add($Symbols)
}
Push-Location $ProjectRoot
try {
    $PlanJson = (& $UvCommand.Source @PlanArguments) -join [Environment]::NewLine
    if ($LASTEXITCODE -ne 0) {
        throw "Could not resolve the canonical MarketBot runtime plan."
    }
    $RuntimePlan = $PlanJson | ConvertFrom-Json
}
finally {
    Pop-Location
}

$ActiveEngineSlots = @($RuntimePlan.active_engine_slots)
$ProcessSpecs = @($RuntimePlan.processes)
$ManualStartProcessNames = @("order-flow", "scalp", "intraday-opportunity")
foreach ($Spec in $ProcessSpecs) {
    $Arguments = @($Spec.arguments)
    if ($Arguments.Count -ge 2 -and $Arguments[0] -eq "run" -and $Arguments[1] -eq "marketbot") {
        $Spec.arguments = @("run", "python", "-m", "app.operator_cli") + $Arguments[2..($Arguments.Count - 1)]
    }
}
$ReadyFiles = @{}
foreach ($Spec in $ProcessSpecs) {
    if (-not [string]::IsNullOrWhiteSpace($Spec.ready_path)) {
        $ReadyFiles[$Spec.name] = [string]$Spec.ready_path
    }
}
$ReadyFiles["long-portfolio-monitor"] = Join-Path -Path $StatusRoot -ChildPath "long-portfolio-monitor.ready.json"

function Test-EngineActive {
    param([string]$Slot)
    return $ActiveEngineSlots -contains $Slot
}

if ($DryRun) {
    [pscustomobject]@{
        mode = "distributed"
        executable = $UvCommand.Source
        environment = $WindowsEnvironment
        working_directory = $ProjectRoot
        active_engine_slots = $ActiveEngineSlots
        startup_batches = $RuntimePlan.startup_batches
        processes = $ProcessSpecs
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

$ProcessSpecsByName = @{}
foreach ($Spec in $ProcessSpecs) {
    $ProcessSpecsByName[$Spec.name] = $Spec
}

function Start-ConfiguredMarketBotProcess {
    param([string]$Name)
    if (-not $ProcessSpecsByName.ContainsKey($Name)) {
        return $null
    }
    return Start-MarketBotProcess -Spec $ProcessSpecsByName[$Name]
}

function Wait-ConfiguredMarketBotReadiness {
    param([string[]]$Names)
    $Paths = @(
        foreach ($Name in $Names) {
            if ($ProcessSpecsByName.ContainsKey($Name) -and $ReadyFiles.ContainsKey($Name)) {
                $ReadyFiles[$Name]
            }
        }
    )
    if ($Paths.Count -gt 0) {
        Wait-MarketBotReadiness -Paths $Paths
    }
}

$Children = [System.Collections.Generic.List[object]]::new()
try {
    $Host.UI.RawUI.WindowTitle = "MarketBot Control"
    Write-Host "Starting independent MarketBot processes..." -ForegroundColor Cyan
    Write-Host "Project: $ProjectRoot"
    Write-Host "Runtime: $ResolvedRuntimeRoot"
    foreach ($Batch in $RuntimePlan.startup_batches) {
        $BatchNames = @($Batch)
        $AutomaticBatchNames = @(
            $BatchNames | Where-Object { $_ -notin $ManualStartProcessNames }
        )
        foreach ($Name in $AutomaticBatchNames) {
            $Child = Start-ConfiguredMarketBotProcess -Name $Name
            if ($null -ne $Child) {
                $Children.Add($Child)
                Write-Host "Started $($Child.name) (PID $($Child.process.Id))"
            }
        }
        Wait-ConfiguredMarketBotReadiness -Names $AutomaticBatchNames
    }

    foreach ($MonitorSpec in @($ProcessSpecs | Where-Object { $_.operator_monitor })) {
        $Child = Start-ConfiguredMarketBotProcess -Name $MonitorSpec.name
        if ($null -ne $Child) {
            $Children.Add($Child)
            Write-Host "Started $($Child.name) (PID $($Child.process.Id))"
            Wait-ConfiguredMarketBotReadiness -Names @($Child.name)
        }
    }

    $AlertChild = $Children | Where-Object { $_.name -eq "alert" } | Select-Object -First 1
    $ConfirmedChild = $Children | Where-Object { $_.name -eq "confirmed-buy-monitor" } | Select-Object -First 1
    if ($null -ne $AlertChild -and $null -ne $ConfirmedChild) {
        Set-MarketBotVerticalWindowLayout `
            -AnalysisProcess $AlertChild.process `
            -ConfirmedBuyProcess $ConfirmedChild.process
    }

    $StreamChild = $Children | Where-Object { $_.name -eq "alpaca-market-stream" } | Select-Object -First 1
    if (Test-EngineActive -Slot "long-portfolio") {
        $LongPortfolioArguments = @(
            "run", "python", "-m", "app.operator_cli", "alerts", "long-portfolio",
            "--ready-path", $ReadyFiles["long-portfolio-monitor"]
        )
        if ($NoBell) {
            $LongPortfolioArguments += "--no-bell"
        }
        $LongPortfolioSpec = [pscustomobject]@{
            name = "long-portfolio-monitor"
            arguments = $LongPortfolioArguments
        }
        $Child = Start-MarketBotProcess -Spec $LongPortfolioSpec
        $Children.Add($Child)
        Write-Host "Started $($Child.name) (PID $($Child.process.Id))"
        Wait-MarketBotReadiness -Paths @($ReadyFiles["long-portfolio-monitor"])
    }
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
    Get-ChildItem -LiteralPath $StatusRoot -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '\.(host\.pid|arguments\.json)$' } |
        Remove-Item -Force -ErrorAction SilentlyContinue
}
