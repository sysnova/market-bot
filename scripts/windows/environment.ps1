# Shared uv environment selection for native Windows launchers.

function Set-MarketBotWindowsEnvironment {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ProjectRoot
    )

    $ResolvedProjectRoot = [System.IO.Path]::GetFullPath($ProjectRoot)
    $EnvironmentPath = [System.IO.Path]::GetFullPath(
        (Join-Path -Path $ResolvedProjectRoot -ChildPath ".venv-windows")
    )
    [System.Environment]::SetEnvironmentVariable(
        "UV_PROJECT_ENVIRONMENT",
        $EnvironmentPath,
        [System.EnvironmentVariableTarget]::Process
    )
    return $EnvironmentPath
}
