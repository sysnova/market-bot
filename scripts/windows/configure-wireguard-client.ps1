[CmdletBinding()]
param(
    [string]$TunnelName = "marketbot-client",
    [string]$ClientAddress = "10.77.77.2/24",
    [string]$HostPublicKey,
    [string]$Endpoint,
    [string]$HostAllowedIp = "10.77.77.1/32",
    [ValidateRange(0, 65535)]
    [int]$PersistentKeepalive = 25,
    [string]$WireGuardRoot = "$env:ProgramFiles\WireGuard",
    [string]$ConfigRoot = "$env:LOCALAPPDATA\MarketBot\WireGuard",
    [switch]$InstallIfMissing,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

function Test-WireGuardKey {
    param([string]$Value)
    return $Value -match '^[A-Za-z0-9+/]{43}=$'
}

function Assert-IPv4Cidr {
    param(
        [string]$Value,
        [string]$Name
    )
    if ($Value -notmatch '^([^/]+)/([0-9]|[12][0-9]|3[0-2])$') {
        throw "$Name must be an IPv4 CIDR value."
    }
    $Address = $Matches[1]
    $Parsed = $null
    if (-not [Net.IPAddress]::TryParse($Address, [ref]$Parsed) -or
        $Parsed.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork) {
        throw "$Name must be an IPv4 CIDR value."
    }
}

function Get-WireGuardPublicKey {
    param(
        [string]$WgPath,
        [string]$PrivateKey,
        [string]$ScratchRoot
    )
    $Token = [Guid]::NewGuid().ToString('N')
    $PrivateInputPath = Join-Path $ScratchRoot "$Token.private.tmp"
    $PublicOutputPath = Join-Path $ScratchRoot "$Token.public.tmp"
    $ErrorOutputPath = Join-Path $ScratchRoot "$Token.error.tmp"
    $TemporaryPaths = @($PrivateInputPath, $PublicOutputPath, $ErrorOutputPath)
    try {
        [IO.File]::WriteAllText($PrivateInputPath, $PrivateKey, [Text.Encoding]::ASCII)
        Protect-PrivateFile -Path $PrivateInputPath
        $Process = Start-Process -FilePath $WgPath -ArgumentList 'pubkey' `
            -NoNewWindow -Wait -PassThru `
            -RedirectStandardInput $PrivateInputPath `
            -RedirectStandardOutput $PublicOutputPath `
            -RedirectStandardError $ErrorOutputPath
        $PublicKey = ([IO.File]::ReadAllText($PublicOutputPath)).Trim()
        $StandardError = ([IO.File]::ReadAllText($ErrorOutputPath)).Trim()
        if ($Process.ExitCode -ne 0) {
            throw "wg.exe pubkey failed: $StandardError"
        }
        return $PublicKey
    }
    finally {
        foreach ($TemporaryPath in $TemporaryPaths) {
            if (Test-Path -LiteralPath $TemporaryPath -PathType Leaf) {
                Remove-Item -LiteralPath $TemporaryPath -Force
            }
        }
    }
}

function Protect-PrivateFile {
    param([string]$Path)
    $CurrentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    & icacls.exe $Path /inheritance:r /grant:r `
        "*${CurrentSid}:F" '*S-1-5-18:F' '*S-1-5-32-544:F' | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Could not restrict the ACL for $Path."
    }
}

if ([string]::IsNullOrWhiteSpace($TunnelName)) {
    throw "TunnelName cannot be empty."
}
Assert-IPv4Cidr -Value $ClientAddress -Name "ClientAddress"
Assert-IPv4Cidr -Value $HostAllowedIp -Name "HostAllowedIp"

$HasHostKey = -not [string]::IsNullOrWhiteSpace($HostPublicKey)
$HasEndpoint = -not [string]::IsNullOrWhiteSpace($Endpoint)
if ($HasHostKey -xor $HasEndpoint) {
    throw "HostPublicKey and Endpoint must be supplied together."
}
if ($HasHostKey -and -not (Test-WireGuardKey -Value $HostPublicKey)) {
    throw "HostPublicKey must be a WireGuard public key (44 base64 characters)."
}
if ($HasEndpoint -and ($Endpoint -match '://' -or $Endpoint -notmatch '^.+:[1-9][0-9]{0,4}$')) {
    throw "Endpoint must use HOST_OR_DDNS:PORT without a URL scheme."
}
if ($HasEndpoint) {
    $EndpointPort = [int]($Endpoint.Substring($Endpoint.LastIndexOf(':') + 1))
    if ($EndpointPort -gt 65535) {
        throw "Endpoint port must be between 1 and 65535."
    }
}

$WgExe = Join-Path $WireGuardRoot "wg.exe"
$PrivateKeyPath = Join-Path $ConfigRoot "$TunnelName.private.key"
$ConfigPath = Join-Path $ConfigRoot "$TunnelName.conf"
$Stage = if ($HasHostKey) { "complete-config" } else { "generate-key" }

if ($DryRun) {
    [pscustomobject]@{
        mode = "dry-run"
        stage = $Stage
        tunnel = $TunnelName
        client_address = $ClientAddress
        private_key_path = $PrivateKeyPath
        config_path = $ConfigPath
        host_allowed_ip = $HostAllowedIp
        endpoint = if ($HasEndpoint) { $Endpoint } else { $null }
        persistent_keepalive = $PersistentKeepalive
        activation = "manual"
        installs_tunnel_service = $false
    } | ConvertTo-Json -Depth 3
    exit 0
}

if (-not (Test-Path -LiteralPath $WgExe -PathType Leaf)) {
    if (-not $InstallIfMissing) {
        throw "WireGuard was not found under $WireGuardRoot. Install it or use -InstallIfMissing."
    }
    $Winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if (-not $Winget) {
        throw "winget.exe is required for -InstallIfMissing."
    }
    & $Winget.Source install --id WireGuard.WireGuard --exact --silent `
        --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) {
        throw "WireGuard installation failed with exit code $LASTEXITCODE."
    }
}
if (-not (Test-Path -LiteralPath $WgExe -PathType Leaf)) {
    throw "wg.exe is unavailable after installation."
}

New-Item -ItemType Directory -Force -Path $ConfigRoot | Out-Null

if (Test-Path -LiteralPath $PrivateKeyPath -PathType Leaf) {
    $PrivateKey = ([IO.File]::ReadAllText($PrivateKeyPath)).Trim()
}
else {
    $PrivateKey = (& $WgExe genkey).Trim()
    if ($LASTEXITCODE -ne 0 -or -not (Test-WireGuardKey -Value $PrivateKey)) {
        throw "wg.exe could not generate the client private key."
    }
    [IO.File]::WriteAllText($PrivateKeyPath, $PrivateKey, [Text.Encoding]::ASCII)
}
if (-not (Test-WireGuardKey -Value $PrivateKey)) {
    throw "The stored client private key has an invalid WireGuard format."
}
Protect-PrivateFile -Path $PrivateKeyPath

$ClientPublicKey = Get-WireGuardPublicKey `
    -WgPath $WgExe `
    -PrivateKey $PrivateKey `
    -ScratchRoot $ConfigRoot
if (-not (Test-WireGuardKey -Value $ClientPublicKey)) {
    throw "wg.exe could not derive the client public key."
}

if (-not $HasHostKey) {
    [pscustomobject]@{
        status = "client-key-ready"
        tunnel = $TunnelName
        client_public_key = $ClientPublicKey
        client_address_requested = $ClientAddress
        private_key_path = $PrivateKeyPath
        private_key_shared = $false
        next_step = "Send only client_public_key to the MarketBot host administrator."
    } | ConvertTo-Json -Depth 3
    exit 0
}

@(
    '[Interface]'
    "PrivateKey = $PrivateKey"
    "Address = $ClientAddress"
    ''
    '[Peer]'
    "PublicKey = $HostPublicKey"
    "AllowedIPs = $HostAllowedIp"
    "Endpoint = $Endpoint"
    "PersistentKeepalive = $PersistentKeepalive"
) | Set-Content -LiteralPath $ConfigPath -Encoding ASCII
Protect-PrivateFile -Path $ConfigPath

[pscustomobject]@{
    status = "client-config-ready"
    tunnel = $TunnelName
    client_public_key = $ClientPublicKey
    client_address = $ClientAddress
    endpoint = $Endpoint
    host_allowed_ip = $HostAllowedIp
    config_path = $ConfigPath
    private_key_shared = $false
    activation = "manual"
    installs_tunnel_service = $false
    nats_endpoint = "nats://$($HostAllowedIp.Split('/')[0]):4222"
    next_step = "Open WireGuard, import config_path, and activate the tunnel manually."
} | ConvertTo-Json -Depth 3
