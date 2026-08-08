[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$ListenPort = 51820,
    [string]$RuleName = "MarketBot WireGuard UDP 51820",
    [switch]$Apply,
    [switch]$Remove
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($RuleName)) {
    throw "RuleName cannot be empty."
}

$ExistingRule = Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue
$Action = if ($Remove) { "remove" } else { "ensure" }

if (-not $Apply) {
    [pscustomobject]@{
        mode = "dry-run"
        action = $Action
        rule_name = $RuleName
        exists = [bool]$ExistingRule
        direction = "Inbound"
        protocol = "UDP"
        public_port = $ListenPort
        private_port = $ListenPort
        remote_address = "Any"
    } | ConvertTo-Json -Depth 3
    exit 0
}

$Principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Administrator privileges are required to modify Windows Firewall."
}

if ($ExistingRule) {
    $ExistingRule | Remove-NetFirewallRule
}

if (-not $Remove) {
    New-NetFirewallRule `
        -DisplayName $RuleName `
        -Description "Allow the MarketBot WireGuard endpoint; router forwards UDP $ListenPort here." `
        -Enabled True `
        -Direction Inbound `
        -Action Allow `
        -Profile Any `
        -Protocol UDP `
        -LocalPort $ListenPort `
        -RemoteAddress Any `
        -EdgeTraversalPolicy Block | Out-Null
}

[pscustomobject]@{
    status = if ($Remove) { "removed" } else { "configured" }
    rule_name = $RuleName
    protocol = "UDP"
    local_port = $ListenPort
} | ConvertTo-Json -Depth 3
