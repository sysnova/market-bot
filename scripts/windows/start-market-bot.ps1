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
    "alerts-v2" = Join-Path -Path $StatusRoot -ChildPath "alert-v2.ready.json"
    "entry-watcher-v2" = Join-Path -Path $StatusRoot -ChildPath "entry-watcher-v2.ready.json"
    "long-term-v2" = Join-Path -Path $StatusRoot -ChildPath "long-term-v2.ready.json"
    "swing-v2" = Join-Path -Path $StatusRoot -ChildPath "swing-v2.ready.json"
    "intraday-v2" = Join-Path -Path $StatusRoot -ChildPath "intraday-v2.ready.json"
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
    $ReadyFiles["alerts-v2"]
)) {
    $AlertArguments.Add($Argument)
}
if ($NoBell) {
    $AlertArguments.Add("--no-bell")
}
$ProcessSpecs.Add((New-ProcessSpec -Name "alerts-v2" -Arguments $AlertArguments.ToArray()))

$EntryWatchArguments = @(
    "run",
    "marketbot",
    "entry-watch",
    "serve",
    "--ready-path",
    $ReadyFiles["entry-watcher-v2"]
)
$ProcessSpecs.Add((New-ProcessSpec -Name "entry-watcher-v2" -Arguments $EntryWatchArguments))

foreach ($Definition in @(
    [pscustomobject]@{ Name = "long-term-v2"; Command = "long" },
    [pscustomobject]@{ Name = "swing-v2"; Command = "swing" },
    [pscustomobject]@{ Name = "intraday-v2"; Command = "intraday" }
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

$StreamArguments = [System.Collections.Generic.List[string]]::new()
foreach ($Argument in @("run", "marketbot", "market", "stream")) {
    $StreamArguments.Add($Argument)
}
Add-SymbolArguments -Arguments $StreamArguments
$ProcessSpecs.Add((New-ProcessSpec -Name "alpaca-market-stream" -Arguments $StreamArguments.ToArray()))

if ($DryRun) {
    [pscustomobject]@{
        mode = "distributed"
        executable = $UvCommand.Source
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
    $StartParameters = @{
        FilePath = $UvCommand.Source
        ArgumentList = $ArgumentLine
        WorkingDirectory = $ProjectRoot
        PassThru = $true
    }
    if ($Spec.name -eq "alerts-v2") {
        $StartParameters["WindowStyle"] = "Normal"
        $StdoutPath = "visible alert window"
        $StderrPath = "visible alert window"
    }
    else {
        $StartParameters["WindowStyle"] = "Hidden"
        $StartParameters["RedirectStandardOutput"] = $StdoutPath
        $StartParameters["RedirectStandardError"] = $StderrPath
    }
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

$Children = [System.Collections.Generic.List[object]]::new()
try {
    Write-Host "Starting independent MarketBot processes..." -ForegroundColor Cyan
    Write-Host "Project: $ProjectRoot"
    Write-Host "Runtime: $ResolvedRuntimeRoot"
    for ($Index = 0; $Index -lt 2; $Index++) {
        $Child = Start-MarketBotProcess -Spec $ProcessSpecs[$Index]
        $Children.Add($Child)
        Write-Host "Started $($Child.name) (PID $($Child.process.Id))"
    }
    Wait-MarketBotReadiness -Paths @(
        $ReadyFiles["alerts-v2"],
        $ReadyFiles["entry-watcher-v2"]
    )

    for ($Index = 2; $Index -lt 5; $Index++) {
        $Child = Start-MarketBotProcess -Spec $ProcessSpecs[$Index]
        $Children.Add($Child)
        Write-Host "Started $($Child.name) (PID $($Child.process.Id))"
    }
    Wait-MarketBotReadiness -Paths @(
        $ReadyFiles["long-term-v2"],
        $ReadyFiles["swing-v2"],
        $ReadyFiles["intraday-v2"]
    )

    $StreamChild = Start-MarketBotProcess -Spec $ProcessSpecs[5]
    $Children.Add($StreamChild)
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
        if (-not $Child.process.HasExited) {
            Stop-Process -Id $Child.process.Id -ErrorAction SilentlyContinue
        }
    }
}
