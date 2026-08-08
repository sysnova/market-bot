[CmdletBinding()]
param(
    [string]$WireGuardHostIp = "10.77.77.1",
    [string]$ContainerName = "marketbot-nats",
    [string]$Image = "nats:2.12-alpine",
    [string]$VolumeName = "marketbot_nats-data",
    [switch]$Apply
)

$ErrorActionPreference = "Stop"
$ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
$ConfigPath = Join-Path $ProjectRoot "configs\nats-server.conf"

if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
    throw "NATS configuration was not found at $ConfigPath."
}
if (-not (Get-Command docker.exe -ErrorAction SilentlyContinue)) {
    throw "docker.exe was not found."
}

$ExistingJson = docker inspect $ContainerName 2>$null
if ($LASTEXITCODE -ne 0) {
    throw "Container $ContainerName does not exist; this script only performs a safe replacement."
}
$Existing = $ExistingJson | ConvertFrom-Json
$DataMount = $Existing[0].Mounts | Where-Object { $_.Destination -eq '/data' } | Select-Object -First 1
if (-not $DataMount -or $DataMount.Type -ne 'volume' -or $DataMount.Name -ne $VolumeName) {
    throw "Expected $ContainerName to use volume $VolumeName at /data; refusing replacement."
}

$Before = $null
try {
    $Jsz = Invoke-RestMethod -Uri "http://127.0.0.1:8222/jsz?streams=true" -TimeoutSec 5
    $MarketBot = $Jsz.account_details[0].stream_detail | Where-Object { $_.name -eq 'MARKETBOT' }
    if ($MarketBot) {
        $Before = [pscustomobject]@{
            messages = $MarketBot.state.messages
            bytes = $MarketBot.state.bytes
            consumers = $MarketBot.state.consumer_count
            first_seq = $MarketBot.state.first_seq
            last_seq = $MarketBot.state.last_seq
        }
    }
}
catch {
    throw "Could not snapshot JetStream state before replacement: $($_.Exception.Message)"
}

$Plan = [pscustomobject]@{
    mode = if ($Apply) { "apply" } else { "dry-run" }
    container = $ContainerName
    image = $Image
    preserved_volume = $VolumeName
    local_nats = "127.0.0.1:4222"
    wireguard_nats = "${WireGuardHostIp}:4222"
    local_monitoring = "127.0.0.1:8222"
    before = $Before
}
if (-not $Apply) {
    $Plan | ConvertTo-Json -Depth 4
    exit 0
}

$WireGuardAddress = Get-NetIPAddress -AddressFamily IPv4 -IPAddress $WireGuardHostIp -ErrorAction SilentlyContinue
if (-not $WireGuardAddress) {
    throw "WireGuard IP $WireGuardHostIp is not assigned. Configure and start WireGuard first."
}

function Start-MarketBotNats {
    param([switch]$IncludeWireGuard)

    $Arguments = @(
        'run', '--detach', '--name', $ContainerName,
        '--restart', 'unless-stopped',
        '--publish', '127.0.0.1:4222:4222',
        '--publish', '127.0.0.1:8222:8222'
    )
    if ($IncludeWireGuard) {
        $Arguments += @('--publish', "${WireGuardHostIp}:4222:4222")
    }
    $Arguments += @(
        '--mount', "type=volume,source=$VolumeName,target=/data",
        '--mount', "type=bind,source=$ConfigPath,target=/etc/nats/marketbot.conf,readonly",
        '--health-cmd', 'wget -qO- http://127.0.0.1:8222/healthz?js-enabled-only=true || exit 1',
        '--health-interval', '5s', '--health-timeout', '3s', '--health-retries', '12',
        $Image, '--config', '/etc/nats/marketbot.conf'
    )
    & docker.exe @Arguments | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "docker run failed for $ContainerName."
    }
}

docker stop $ContainerName | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Could not stop $ContainerName."
}
docker rm $ContainerName | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Could not remove the stopped container $ContainerName. The volume was not removed."
}

try {
    Start-MarketBotNats -IncludeWireGuard
}
catch {
    Write-Warning "WireGuard publication failed. Restoring a localhost-only NATS container."
    docker rm --force $ContainerName 2>$null | Out-Null
    Start-MarketBotNats
    throw
}

$Healthy = $false
for ($Attempt = 0; $Attempt -lt 20; $Attempt++) {
    Start-Sleep -Seconds 1
    $Health = docker inspect $ContainerName --format '{{.State.Health.Status}}' 2>$null
    if ($Health -eq 'healthy') {
        $Healthy = $true
        break
    }
}
if (-not $Healthy) {
    throw "$ContainerName did not become healthy; inspect docker logs $ContainerName."
}

$AfterJsz = Invoke-RestMethod -Uri "http://127.0.0.1:8222/jsz?streams=true" -TimeoutSec 5
$AfterStream = $AfterJsz.account_details[0].stream_detail | Where-Object { $_.name -eq 'MARKETBOT' }
$After = [pscustomobject]@{
    messages = $AfterStream.state.messages
    bytes = $AfterStream.state.bytes
    consumers = $AfterStream.state.consumer_count
    first_seq = $AfterStream.state.first_seq
    last_seq = $AfterStream.state.last_seq
}
if ($Before -and $After.last_seq -lt $Before.last_seq) {
    throw "JetStream last sequence regressed after replacement. Stop and inspect the preserved volume."
}

[pscustomobject]@{
    status = "recreated"
    container = $ContainerName
    preserved_volume = $VolumeName
    endpoints = @("127.0.0.1:4222", "${WireGuardHostIp}:4222", "127.0.0.1:8222")
    before = $Before
    after = $After
} | ConvertTo-Json -Depth 4
