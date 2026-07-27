# Starts the analysis-only MarketBot from any PowerShell working directory.
[CmdletBinding()]
param(
    [string]$Symbols,
    [switch]$Once,
    [switch]$NoNats,
    [switch]$NoBell,
    [string]$RuntimeRoot = ".runtime",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$ProjectRoot = [System.IO.Path]::GetFullPath(
    (Join-Path -Path $PSScriptRoot -ChildPath "..\..")
)

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
        arguments = $MarketBotArguments.ToArray()
        working_directory = $ProjectRoot
    } | ConvertTo-Json -Depth 3
    exit 0
}

Write-Host "Starting MarketBot (analysis and local alerts only)..." -ForegroundColor Cyan
Write-Host "Project: $ProjectRoot"
if ([string]::IsNullOrWhiteSpace($Symbols)) {
    Write-Host "Universe: Supabase configuration"
}
else {
    Write-Host "Universe: $Symbols"
}
Write-Host "Press Ctrl+C to stop."

Push-Location $ProjectRoot
try {
    & $UvCommand.Source @MarketBotArguments
    $MarketBotExitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}

exit $MarketBotExitCode
