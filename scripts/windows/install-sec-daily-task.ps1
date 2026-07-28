# Registers the independent SEC bot as a daily task for the current Windows user.
[CmdletBinding()]
param(
    [string]$TaskName = "MarketBotSECDaily",
    [ValidatePattern("^([01]\d|2[0-3]):[0-5]\d$")]
    [string]$At = "20:00",
    [ValidateRange(1, 30)]
    [int]$LookbackDays = 2,
    [switch]$NoNats,
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$RunnerPath = [System.IO.Path]::GetFullPath(
    (Join-Path -Path $PSScriptRoot -ChildPath "run-sec-bot.ps1")
)
if (-not (Test-Path -LiteralPath $RunnerPath -PathType Leaf)) {
    throw "SEC runner not found: $RunnerPath"
}

$ExistingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -ne $ExistingTask -and -not $Force) {
    throw "Scheduled task $TaskName already exists. Use -Force to update it."
}

$PowerShellPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$ActionArguments = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", ('"{0}"' -f $RunnerPath),
    "-LookbackDays", [string]$LookbackDays
)
if ($NoNats) {
    $ActionArguments += "-NoNats"
}

$Action = New-ScheduledTaskAction -Execute $PowerShellPath -Argument ($ActionArguments -join " ")
$Trigger = New-ScheduledTaskTrigger -Daily -At $At
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2)

$Registration = @{
    TaskName = $TaskName
    Action = $Action
    Trigger = $Trigger
    Settings = $Settings
    Description = "MarketBot bounded SEC dilution scan for recent filings"
}
if ($Force) {
    $Registration["Force"] = $true
}

Register-ScheduledTask @Registration | Out-Null
Write-Host "Scheduled task $TaskName installed for $At every day." -ForegroundColor Green
Write-Host "The scan covers the latest $LookbackDays filing day(s) and submits no orders."
