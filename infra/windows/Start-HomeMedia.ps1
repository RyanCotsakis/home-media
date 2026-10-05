[CmdletBinding()]
param(
    [string]$DistroName = "Ubuntu-26.04",
    [int]$EngineTimeoutSeconds = 240,
    [switch]$OpenJellyfin
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$LinuxLauncher = "/home/ryan/home_cotsakis/infra/docker/ensure-stack.sh"
$LogDirectory = Join-Path $env:LOCALAPPDATA "HomeMedia"
$LogFile = Join-Path $LogDirectory "launcher.log"
New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null
# Keep one clean, readable log for the most recent launch. Windows PowerShell
# otherwise mixes UTF-16 Tee-Object output with UTF-8 status lines.
Set-Content -LiteralPath $LogFile -Value "" -Encoding UTF8

function Write-Status {
    param([string]$Message)
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Write-Host $line -ForegroundColor Cyan
    Add-Content -LiteralPath $LogFile -Value $line -Encoding UTF8
}

function Invoke-WslDockerInfo {
    # Before Docker Desktop starts, its WSL CLI may not exist yet. That is an
    # expected false result, not a fatal PowerShell NativeCommandError.
    $previousErrorPreference = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    try {
        $output = & wsl.exe --distribution $DistroName --exec bash -lc 'docker info --format "{{.ServerVersion}}"' 2>$null
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorPreference
    }
    return ($exitCode -eq 0 -and -not [string]::IsNullOrWhiteSpace(($output | Out-String)))
}

try {
    Write-Status "Home Media startup requested"

    $availableDistros = @(& wsl.exe --list --quiet 2>$null) | ForEach-Object { $_.Trim() } | Where-Object { $_ }
    if ($availableDistros -notcontains $DistroName) {
        throw "WSL distribution '$DistroName' was not found. Available: $($availableDistros -join ', ')"
    }

    $dockerDesktopCandidates = @(
        (Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"),
        (Join-Path $env:LOCALAPPDATA "Docker\Docker Desktop.exe")
    )
    $dockerDesktop = $dockerDesktopCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $dockerDesktop) {
        throw "Docker Desktop.exe was not found in its standard installation locations."
    }

    if (-not (Invoke-WslDockerInfo)) {
        Write-Status "Starting Docker Desktop"
        # Launching the executable is safe even if the UI process already
        # exists and also wakes an installed-but-not-ready engine.
        Start-Process -FilePath $dockerDesktop | Out-Null

        $deadline = (Get-Date).AddSeconds($EngineTimeoutSeconds)
        do {
            if ((Get-Date) -ge $deadline) {
                throw "Docker Engine did not become available within $EngineTimeoutSeconds seconds."
            }
            Write-Status "Waiting for Docker Engine and WSL integration"
            Start-Sleep -Seconds 4
        } until (Invoke-WslDockerInfo)
    }

    Write-Status "Docker Engine is ready; validating the home-media stack"
    # Windows PowerShell 5.1 wraps native stderr lines as ErrorRecord objects.
    # Docker Compose uses stderr for ordinary progress, so judge this native
    # boundary by its exit code while still capturing both streams in the log.
    $previousErrorPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & wsl.exe --distribution $DistroName --exec bash $LinuxLauncher |
        ForEach-Object {
            $line = $_ | Out-String
            Write-Host $line.TrimEnd()
            Add-Content -LiteralPath $LogFile -Value $line.TrimEnd() -Encoding UTF8
        }
    $stackExitCode = $LASTEXITCODE
    $ErrorActionPreference = $previousErrorPreference
    if ($stackExitCode -ne 0) {
        throw "The home-media health check failed with exit code $stackExitCode."
    }

    Write-Status "Home Media is ready"
    if ($OpenJellyfin) {
        Start-Process "http://localhost:8096/web/"
    }
    Write-Host "`nAll services are healthy. This window will close in 5 seconds." -ForegroundColor Green
    Start-Sleep -Seconds 5
    exit 0
}
catch {
    $message = "Startup failed: $($_.Exception.Message)"
    Write-Host "`n$message" -ForegroundColor Red
    Add-Content -LiteralPath $LogFile -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $message" -Encoding UTF8
    Write-Host "Log: $LogFile" -ForegroundColor Yellow
    Read-Host "Press Enter to close"
    exit 1
}
