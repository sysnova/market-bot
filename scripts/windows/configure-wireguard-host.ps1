[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ClientPublicKey,
    [string]$TunnelName = "marketbot",
    [string]$HostAddress = "10.77.77.1/24",
    [string]$ClientAddress = "10.77.77.2/32",
    [int]$ListenPort = 51820,
    [string]$WireGuardRoot = "$env:ProgramFiles\WireGuard",
    [string]$ConfigRoot = "$env:ProgramData\MarketBot\WireGuard",
    [switch]$InstallIfMissing,
    [switch]$PrepareOnly,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

function Test-WireGuardPublicKey {
    param([string]$Value)
    return $Value -match '^[A-Za-z0-9+/]{43}=$'
}

function Get-WireGuardPublicKey {
    param(
        [string]$WgPath,
        [string]$PrivateKey,
        [string]$ScratchRoot
    )
    # Windows PowerShell 5 changes native-pipeline encodings. Redirect exact ASCII
    # files instead so wg.exe receives only the 44 key bytes.
    $Token = [Guid]::NewGuid().ToString('N')
    $PrivateInputPath = Join-Path $ScratchRoot "$Token.private.tmp"
    $PublicOutputPath = Join-Path $ScratchRoot "$Token.public.tmp"
    $ErrorOutputPath = Join-Path $ScratchRoot "$Token.error.tmp"
    $TemporaryPaths = @($PrivateInputPath, $PublicOutputPath, $ErrorOutputPath)
    try {
        [IO.File]::WriteAllText($PrivateInputPath, $PrivateKey, [Text.Encoding]::ASCII)
        & icacls.exe $PrivateInputPath /inheritance:r /grant:r `
            "*$([Security.Principal.WindowsIdentity]::GetCurrent().User.Value):F" `
            '*S-1-5-18:F' '*S-1-5-32-544:F' | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "Could not restrict the temporary WireGuard private-key ACL."
        }
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

if (-not (Test-WireGuardPublicKey -Value $ClientPublicKey)) {
    throw "ClientPublicKey must be a WireGuard public key (44 base64 characters)."
}
if ($ListenPort -lt 1 -or $ListenPort -gt 65535) {
    throw "ListenPort must be between 1 and 65535."
}

$WireGuardExe = Join-Path $WireGuardRoot "wireguard.exe"
$WgExe = Join-Path $WireGuardRoot "wg.exe"
$ConfigPath = Join-Path $ConfigRoot "$TunnelName.conf"
$HostIp = $HostAddress.Split('/')[0]
$ClientIp = $ClientAddress.Split('/')[0]

if ($DryRun) {
    [pscustomobject]@{
        mode = "dry-run"
        tunnel = $TunnelName
        config_path = $ConfigPath
        host_address = $HostAddress
        client_allowed_ip = $ClientAddress
        listen_udp = $ListenPort
        nats_endpoint = "nats://${HostIp}:4222"
        activation = "manual"
        installs_tunnel_service = $false
        prepare_only = [bool]$PrepareOnly
    } | ConvertTo-Json -Depth 3
    exit 0
}

$Identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$Principal = [Security.Principal.WindowsPrincipal]$Identity
if (-not $PrepareOnly -and -not $Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Administrator privileges are required to configure WireGuard and Windows Firewall."
}

if ((-not (Test-Path -LiteralPath $WireGuardExe)) -or (-not (Test-Path -LiteralPath $WgExe))) {
    if (-not $InstallIfMissing) {
        throw "WireGuard was not found under $WireGuardRoot. Install it or use -InstallIfMissing."
    }
    if ($PrepareOnly) {
        throw "PrepareOnly cannot install WireGuard; install it first or run without PrepareOnly as administrator."
    }
    $Winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if (-not $Winget) {
        throw "winget.exe is required for -InstallIfMissing."
    }
    & $Winget.Source install --id WireGuard.WireGuard --exact --silent --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) {
        throw "WireGuard installation failed with exit code $LASTEXITCODE."
    }
}
if ((-not (Test-Path -LiteralPath $WireGuardExe)) -or (-not (Test-Path -LiteralPath $WgExe))) {
    throw "WireGuard executables are unavailable after installation."
}

New-Item -ItemType Directory -Force -Path $ConfigRoot | Out-Null

$PrivateKey = $null
if (Test-Path -LiteralPath $ConfigPath -PathType Leaf) {
    $ExistingPrivateKey = Get-Content -LiteralPath $ConfigPath |
        Where-Object { $_ -match '^\s*PrivateKey\s*=\s*(.+)\s*$' } |
        Select-Object -First 1
    if ($ExistingPrivateKey -and $ExistingPrivateKey -match '^\s*PrivateKey\s*=\s*(.+)\s*$') {
        $PrivateKey = $Matches[1].Trim()
    }
}
if (-not $PrivateKey) {
    $PrivateKey = (& $WgExe genkey).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $PrivateKey) {
        throw "wg.exe could not generate the host private key."
    }
}
if (-not (Test-WireGuardPublicKey -Value $PrivateKey)) {
    throw "Host private key has an invalid WireGuard format."
}
$ServerPublicKey = Get-WireGuardPublicKey -WgPath $WgExe -PrivateKey $PrivateKey -ScratchRoot $ConfigRoot
if (-not (Test-WireGuardPublicKey -Value $ServerPublicKey)) {
    throw "wg.exe could not derive the host public key."
}

@(
    '[Interface]'
    "PrivateKey = $PrivateKey"
    "Address = $HostAddress"
    "ListenPort = $ListenPort"
    ''
    '[Peer]'
    "PublicKey = $ClientPublicKey"
    "AllowedIPs = $ClientAddress"
) | Set-Content -LiteralPath $ConfigPath -Encoding ASCII

$AclGrants = @('*S-1-5-18:F', '*S-1-5-32-544:F')
if ($PrepareOnly) {
    $AclGrants += "*$($Identity.User.Value):F"
}
& icacls.exe $ConfigPath /inheritance:r /grant:r $AclGrants | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Could not restrict the WireGuard configuration ACL."
}

if ($PrepareOnly) {
    [pscustomobject]@{
        status = "prepared-without-firewall"
        tunnel = $TunnelName
        host_public_key = $ServerPublicKey
        host_address = $HostAddress
        client_allowed_ip = $ClientAddress
        listen_udp = $ListenPort
        nats_endpoint = "nats://${HostIp}:4222"
        activation = "manual"
        installs_tunnel_service = $false
        firewall_configured = $false
        config_path = $ConfigPath
        next_step = "Run this script without PrepareOnly as administrator, then import and activate the tunnel manually."
    } | ConvertTo-Json -Depth 3
    exit 0
}

$UdpRule = "MarketBot WireGuard UDP $ListenPort"
$NatsRule = "MarketBot NATS from WireGuard peer"
Remove-NetFirewallRule -DisplayName $UdpRule -ErrorAction SilentlyContinue
Remove-NetFirewallRule -DisplayName $NatsRule -ErrorAction SilentlyContinue
New-NetFirewallRule -DisplayName $UdpRule -Direction Inbound -Action Allow `
    -Protocol UDP -LocalPort $ListenPort -Profile Any | Out-Null
New-NetFirewallRule -DisplayName $NatsRule -Direction Inbound -Action Allow `
    -Protocol TCP -LocalAddress $HostIp -LocalPort 4222 -RemoteAddress $ClientIp -Profile Any | Out-Null

[pscustomobject]@{
    status = "prepared"
    tunnel = $TunnelName
    host_public_key = $ServerPublicKey
    host_address = $HostAddress
    client_allowed_ip = $ClientAddress
    listen_udp = $ListenPort
    nats_endpoint = "nats://${HostIp}:4222"
    activation = "manual"
    installs_tunnel_service = $false
    config_path = $ConfigPath
    next_step = "Open WireGuard, import the config_path file, and activate the tunnel manually."
    client_template = "configs/wireguard/marketbot-client.conf.example"
} | ConvertTo-Json -Depth 3
