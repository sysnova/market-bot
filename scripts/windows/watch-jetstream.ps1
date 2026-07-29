# Watches live MarketBot messages published through NATS/JetStream.
[CmdletBinding()]
param(
    [string]$Subject = "marketbot.>",
    [string]$NatsUrl = "nats://127.0.0.1:4222",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$ProjectRoot = [System.IO.Path]::GetFullPath(
    (Join-Path -Path $PSScriptRoot -ChildPath "..\..")
)
$Python = Join-Path -Path $ProjectRoot -ChildPath ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "MarketBot virtual environment was not found at $Python"
}
if ([string]::IsNullOrWhiteSpace($Subject)) {
    throw "Subject cannot be blank."
}
if ([string]::IsNullOrWhiteSpace($NatsUrl)) {
    throw "NatsUrl cannot be blank."
}

if ($DryRun) {
    [pscustomobject]@{
        executable = $Python
        nats_url = $NatsUrl
        subject = $Subject
        working_directory = $ProjectRoot
    } | ConvertTo-Json -Depth 2
    exit 0
}

$MonitorSource = @'
import asyncio
import json
import selectors
import sys
from datetime import UTC, datetime

from nats.aio.client import Client as NATS


async def main() -> None:
    nats_url, subject = sys.argv[1:3]
    client = NATS()
    await client.connect(nats_url, name="marketbot-powershell-monitor")

    async def show(message) -> None:
        timestamp = datetime.now(UTC).isoformat()
        try:
            payload = json.loads(message.data)
            body = json.dumps(payload, indent=2, ensure_ascii=False)
        except (UnicodeDecodeError, json.JSONDecodeError):
            body = message.data.decode(errors="replace")
        print(f"\n[{timestamp}] {message.subject}\n{body}", flush=True)

    await client.subscribe(subject, cb=show)
    print(f"Listening on {subject} through {nats_url} — Ctrl+C to stop", flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await client.drain()


asyncio.run(
    main(),
    loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
)
'@

Push-Location $ProjectRoot
try {
    $MonitorSource | & $Python - $NatsUrl $Subject
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
