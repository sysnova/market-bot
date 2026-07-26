[CmdletBinding()]
param(
    [string]$Version = "2.14.3",
    [string]$ExpectedSha256 = "94e338d742761272e31eab1efb1f767eac3a2e56e4c05a7933c65a73fe95a27b",
    [string]$ServiceName = "MarketBotNATS",
    [string]$InstallRoot = "$env:ProgramFiles\MarketBot\NATS",
    [string]$DataRoot = "$env:ProgramData\MarketBot\NATS",
    [string]$ResultPath = ""
)

$ErrorActionPreference = "Stop"

function Invoke-ServiceControl {
    param([string[]]$ServiceArguments)

    $serviceOutput = & sc.exe @ServiceArguments 2>&1
    $serviceExitCode = $LASTEXITCODE
    if ($serviceExitCode -ne 0) {
        $renderedOutput = ($serviceOutput | Out-String).Trim()
        throw "sc.exe $($ServiceArguments -join ' ') failed with exit code ${serviceExitCode}: $renderedOutput"
    }
    return $serviceOutput
}

function Write-InstallResult {
    param(
        [string]$Status,
        [string]$Message
    )

    if (-not $ResultPath) {
        return
    }
    $resultDirectory = Split-Path -Parent $ResultPath
    if ($resultDirectory) {
        New-Item -ItemType Directory -Force -Path $resultDirectory | Out-Null
    }
    [pscustomobject]@{
        status = $Status
        message = $Message
        service = $ServiceName
        version = $Version
        recorded_at = [DateTimeOffset]::UtcNow.ToString("O")
    } | ConvertTo-Json | Set-Content -LiteralPath $ResultPath -Encoding UTF8
}

try {
    $principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Administrator privileges are required to install a Windows service."
    }

    if (-not [Environment]::Is64BitOperatingSystem) {
        throw "MarketBot's NATS service installer requires 64-bit Windows."
    }

    $assetName = "nats-server-v$Version-windows-amd64.zip"
    $downloadUri = "https://github.com/nats-io/nats-server/releases/download/v$Version/$assetName"
    $temporaryRoot = Join-Path ([IO.Path]::GetTempPath()) ("marketbot-nats-" + [Guid]::NewGuid())
    $archivePath = Join-Path $temporaryRoot $assetName
    $expandedPath = Join-Path $temporaryRoot "expanded"

    New-Item -ItemType Directory -Force -Path $temporaryRoot, $expandedPath | Out-Null
    try {
        Invoke-WebRequest -Uri $downloadUri -OutFile $archivePath -UseBasicParsing
        $actualSha256 = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actualSha256 -ne $ExpectedSha256.ToLowerInvariant()) {
            throw "NATS archive checksum mismatch. Expected $ExpectedSha256, received $actualSha256."
        }

        Expand-Archive -LiteralPath $archivePath -DestinationPath $expandedPath
        $downloadedBinary = Get-ChildItem -LiteralPath $expandedPath -Recurse -Filter "nats-server.exe" |
            Select-Object -First 1
        if (-not $downloadedBinary) {
            throw "The verified NATS archive did not contain nats-server.exe."
        }

        $existingService = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
        if ($existingService -and $existingService.Status -ne "Stopped") {
            Stop-Service -Name $ServiceName -Force
            $existingService.WaitForStatus("Stopped", [TimeSpan]::FromSeconds(20))
        }

        New-Item -ItemType Directory -Force -Path $InstallRoot, $DataRoot | Out-Null
        foreach ($directory in @("data", "logs", "run")) {
            New-Item -ItemType Directory -Force -Path (Join-Path $DataRoot $directory) | Out-Null
        }

        $binaryPath = Join-Path $InstallRoot "nats-server.exe"
        $configPath = Join-Path $DataRoot "nats-server.conf"
        Copy-Item -LiteralPath $downloadedBinary.FullName -Destination $binaryPath -Force

        $portableDataRoot = $DataRoot.Replace("\", "/")
        @(
            'server_name: "marketbot-local"'
            'listen: "127.0.0.1:4222"'
            'http: "127.0.0.1:8222"'
            ''
            'jetstream {'
            "  store_dir: `"$portableDataRoot/data`""
            '  max_mem_store: 512MB'
            '  max_file_store: 10GB'
            '}'
            ''
            "pid_file: `"$portableDataRoot/run/nats-server.pid`""
            "log_file: `"$portableDataRoot/logs/nats-server.log`""
            'logtime: true'
        ) | Set-Content -LiteralPath $configPath -Encoding ASCII

        & $binaryPath -t -c $configPath
        if ($LASTEXITCODE -ne 0) {
            throw "NATS rejected the generated configuration."
        }

        & icacls.exe $InstallRoot /grant '*S-1-5-20:(OI)(CI)RX' /T /C | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "Could not grant NetworkService read access to the NATS binary."
        }
        & icacls.exe $DataRoot /grant '*S-1-5-20:(OI)(CI)M' /T /C | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "Could not grant NetworkService write access to the NATS data directory."
        }

        $serviceCommand = "`"$binaryPath`" -c `"$configPath`""
        if ($existingService) {
            $serviceRecord = Get-CimInstance -ClassName Win32_Service -Filter "Name='$ServiceName'"
            $serviceChange = Invoke-CimMethod -InputObject $serviceRecord -MethodName Change -Arguments @{
                DisplayName = "MarketBot NATS JetStream"
                PathName = $serviceCommand
                StartMode = "Automatic"
                StartName = 'NT AUTHORITY\NetworkService'
                StartPassword = ""
            }
            if ($serviceChange.ReturnValue -ne 0) {
                throw "Win32_Service.Change failed with code $($serviceChange.ReturnValue)."
            }
        }
        else {
            $serviceCreation = Invoke-CimMethod -ClassName Win32_Service -MethodName Create -Arguments @{
                Name = $ServiceName
                DisplayName = "MarketBot NATS JetStream"
                PathName = $serviceCommand
                ServiceType = [byte]16
                ErrorControl = [byte]1
                StartMode = "Automatic"
                DesktopInteract = $false
                StartName = 'NT AUTHORITY\NetworkService'
                StartPassword = ""
            }
            if ($serviceCreation.ReturnValue -ne 0) {
                throw "Win32_Service.Create failed with code $($serviceCreation.ReturnValue)."
            }
        }

        Invoke-ServiceControl @("config", $ServiceName, "start=", "delayed-auto") | Out-Null
        Invoke-ServiceControl @(
            "description", $ServiceName, "Local NATS JetStream broker for MarketBot."
        ) | Out-Null
        Invoke-ServiceControl @(
            "failure", $ServiceName,
            "reset=", "86400",
            "actions=", "restart/5000/restart/15000/none/0"
        ) | Out-Null

        Start-Service -Name $ServiceName
        $service = Get-Service -Name $ServiceName
        $service.WaitForStatus("Running", [TimeSpan]::FromSeconds(20))

        $deadline = [DateTimeOffset]::UtcNow.AddSeconds(20)
        do {
            try {
                $health = Invoke-RestMethod -Uri "http://127.0.0.1:8222/healthz?js-enabled-only=true"
                break
            }
            catch {
                if ([DateTimeOffset]::UtcNow -ge $deadline) {
                    throw
                }
                Start-Sleep -Milliseconds 500
            }
        } while ($true)

        $installedVersion = (& $binaryPath --version) -join " "
        Write-InstallResult -Status "installed" -Message "$installedVersion; health=$health"
    }
    finally {
        $resolvedTemporaryRoot = [IO.Path]::GetFullPath($temporaryRoot)
        $resolvedSystemTemp = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
        if ($resolvedTemporaryRoot.StartsWith($resolvedSystemTemp, [StringComparison]::OrdinalIgnoreCase) -and
            (Test-Path -LiteralPath $resolvedTemporaryRoot)) {
            Remove-Item -LiteralPath $resolvedTemporaryRoot -Recurse -Force
        }
    }
}
catch {
    Write-InstallResult -Status "failed" -Message $_.Exception.Message
    throw
}
