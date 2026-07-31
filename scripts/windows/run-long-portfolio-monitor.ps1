[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ReadyPath,
    [switch]$NoBell
)

$ErrorActionPreference = "Stop"
$ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
Set-Location -LiteralPath $ProjectRoot
$Arguments = @(
    "run", "marketbot", "alerts", "long-portfolio", "--ready-path", $ReadyPath
)
if ($NoBell) {
    $Arguments += "--no-bell"
}
& uv @Arguments
exit $LASTEXITCODE
