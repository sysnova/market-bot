[CmdletBinding()]
param(
    [string]$SessionName = "marketbot-long",
    [string]$ReadyPath = ".runtime/status/long-portfolio-monitor.ready.json",
    [switch]$NoBell,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
$ResolvedReadyPath = if ([System.IO.Path]::IsPathRooted($ReadyPath)) {
    [System.IO.Path]::GetFullPath($ReadyPath)
}
else {
    [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot $ReadyPath))
}
$BellArgument = if ($NoBell) { " -NoBell" } else { "" }
$EscapedReady = $ResolvedReadyPath.Replace("'", "''")
$MonitorScript = [System.IO.Path]::GetFullPath(
    (Join-Path $PSScriptRoot "run-long-portfolio-monitor.ps1")
).Replace("'", "''")
$PaneCommand = "powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File '$MonitorScript' -ReadyPath '$EscapedReady'$BellArgument"

if ($DryRun) {
    [pscustomobject]@{
        session = $SessionName
        pane_command = $PaneCommand
        ready_path = $ResolvedReadyPath
    } | ConvertTo-Json -Depth 3
    exit 0
}

if ($null -eq (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    throw "WSL is required for the LONG portfolio tmux monitor."
}
& wsl.exe sh -lc "command -v tmux >/dev/null"
if ($LASTEXITCODE -ne 0) {
    throw "tmux is not installed in the default WSL distribution."
}

$SessionExists = (& wsl.exe sh -lc (
    "tmux has-session -t '$SessionName' 2>/dev/null && echo yes || echo no"
)) -eq "yes"
if (-not $SessionExists) {
    & wsl.exe tmux new-session -d -s $SessionName $PaneCommand
    if ($LASTEXITCODE -ne 0) {
        throw "Could not create tmux session $SessionName."
    }
}

$WindowsTerminal = Get-Command wt.exe -ErrorAction Stop
$TerminalArguments = @(
    "-w", "-1", "new-tab", "--title", "MarketBot LONG Portfolio",
    "wsl.exe", "tmux", "attach-session", "-t", $SessionName
)
$TerminalArgumentLine = ($TerminalArguments | ForEach-Object {
    '"' + ([string]$_).Replace('"', '\"') + '"'
}) -join " "
Start-Process -FilePath $WindowsTerminal.Source -ArgumentList $TerminalArgumentLine | Out-Null
