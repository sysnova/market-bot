# Hosts one visible MarketBot process and exposes its PID to the supervisor.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("analysis", "confirmed")]
    [string]$Role,
    [Parameter(Mandatory = $true)]
    [string]$PidPath,
    [Parameter(Mandatory = $true)]
    [string]$Executable,
    [Parameter(Mandatory = $true)]
    [string]$ArgumentsPath
)

$ErrorActionPreference = "Stop"
$ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path -Path $PSScriptRoot -ChildPath "..\.."))
. (Join-Path -Path $PSScriptRoot -ChildPath "environment.ps1")
$null = Set-MarketBotWindowsEnvironment -ProjectRoot $ProjectRoot
$WindowTitle = if ($Role -eq "analysis") {
    "MarketBot Analysis"
}
else {
    "MarketBot Confirmed Buys"
}
$Host.UI.RawUI.WindowTitle = $WindowTitle
$ProcessArguments = @(Get-Content -LiteralPath $ArgumentsPath -Raw | ConvertFrom-Json)
[System.IO.File]::WriteAllText($PidPath, [string]$PID)
try {
    & $Executable @ProcessArguments
    exit $LASTEXITCODE
}
finally {
    Remove-Item -LiteralPath $PidPath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $ArgumentsPath -Force -ErrorAction SilentlyContinue
}
