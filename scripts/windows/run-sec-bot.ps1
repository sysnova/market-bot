# Runs the independent recent-filings SEC bot once.
[CmdletBinding()]
param(
    [ValidateRange(1, 30)]
    [int]$LookbackDays = 2,
    [string]$Symbols,
    [switch]$NoNats,
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

$SecArguments = [System.Collections.Generic.List[string]]::new()
foreach ($Argument in @(
    "run",
    "marketbot",
    "sec",
    "daily",
    "--lookback-days",
    [string]$LookbackDays,
    "--runtime-root",
    $ResolvedRuntimeRoot
)) {
    $SecArguments.Add($Argument)
}
if ($NoNats) {
    $SecArguments.Add("--no-nats")
}
if (-not [string]::IsNullOrWhiteSpace($Symbols)) {
    $SecArguments.Add("--symbols")
    $SecArguments.Add($Symbols)
}

if ($DryRun) {
    [pscustomobject]@{
        executable = $UvCommand.Source
        arguments = $SecArguments.ToArray()
        working_directory = $ProjectRoot
    } | ConvertTo-Json -Depth 3
    exit 0
}

Write-Host "Starting bounded SEC daily scan..." -ForegroundColor Cyan
Write-Host "Project: $ProjectRoot"
Write-Host "Filing window: last $LookbackDays day(s), inclusive"
if ([string]::IsNullOrWhiteSpace($Symbols)) {
    Write-Host "Universe: local PostgreSQL configuration"
}
else {
    Write-Host "Universe: $Symbols"
}

Push-Location $ProjectRoot
try {
    & $UvCommand.Source @SecArguments
    $SecBotExitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}

exit $SecBotExitCode
