# Creates or refreshes the native Windows MarketBot environment.
[CmdletBinding()]
param(
    [switch]$Recreate,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$ProjectRoot = [System.IO.Path]::GetFullPath(
    (Join-Path -Path $PSScriptRoot -ChildPath "..\..")
)
. (Join-Path -Path $PSScriptRoot -ChildPath "environment.ps1")
$EnvironmentPath = Set-MarketBotWindowsEnvironment -ProjectRoot $ProjectRoot

$UvCommand = Get-Command uv -ErrorAction SilentlyContinue
if ($null -eq $UvCommand) {
    throw "uv is not installed or is not available in PATH. Install uv and reopen PowerShell."
}

if ($DryRun) {
    [pscustomobject]@{
        executable = $UvCommand.Source
        environment = $EnvironmentPath
        python = "3.14"
        sync_arguments = @("sync", "--locked")
        working_directory = $ProjectRoot
    } | ConvertTo-Json -Depth 3
    exit 0
}

if ($Recreate -and (Test-Path -LiteralPath $EnvironmentPath)) {
    $EnvironmentParent = [System.IO.Path]::GetFullPath(
        (Split-Path -Path $EnvironmentPath -Parent)
    )
    if ($EnvironmentParent -ne $ProjectRoot) {
        throw "Refusing to remove an environment outside the MarketBot project: $EnvironmentPath"
    }
    Write-Host "Removing replaceable Windows environment: $EnvironmentPath" -ForegroundColor Yellow
    Remove-Item -LiteralPath $EnvironmentPath -Recurse -Force
}

Push-Location $ProjectRoot
try {
    Write-Host "Installing the locked MarketBot environment for native Windows..." -ForegroundColor Cyan
    & $UvCommand.Source python install 3.14
    if ($LASTEXITCODE -ne 0) {
        throw "uv could not install Python 3.14."
    }
    & $UvCommand.Source sync --locked
    if ($LASTEXITCODE -ne 0) {
        throw "uv could not synchronize the locked MarketBot dependencies."
    }
}
finally {
    Pop-Location
}

$Python = Join-Path -Path $EnvironmentPath -ChildPath "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "The Windows environment was created without its expected interpreter: $Python"
}

$Version = & $Python --version
Write-Host "MarketBot Windows environment ready." -ForegroundColor Green
Write-Host "Environment: $EnvironmentPath"
Write-Host "Interpreter: $Version"
