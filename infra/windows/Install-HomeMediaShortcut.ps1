[CmdletBinding()]
param(
    [string]$DistroName = "Ubuntu-26.04"
)

$ErrorActionPreference = "Stop"
$launcher = Join-Path $PSScriptRoot "Start-HomeMedia.ps1"
if (-not (Test-Path -LiteralPath $launcher)) {
    throw "Launcher not found: $launcher"
}

$powershell = Join-Path $PSHOME "powershell.exe"
$dockerDesktop = Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
$icon = if (Test-Path -LiteralPath $dockerDesktop) { "$dockerDesktop,0" } else { "$powershell,0" }
$arguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -DistroName "{1}" -OpenJellyfin' -f $launcher, $DistroName
$shell = New-Object -ComObject WScript.Shell

$destinations = @(
    (Join-Path ([Environment]::GetFolderPath("Desktop")) "Home Media Server.lnk"),
    (Join-Path ([Environment]::GetFolderPath("Programs")) "Home Media Server.lnk")
)

foreach ($destination in $destinations) {
    $shortcut = $shell.CreateShortcut($destination)
    $shortcut.TargetPath = $powershell
    $shortcut.Arguments = $arguments
    $shortcut.WorkingDirectory = $env:USERPROFILE
    $shortcut.Description = "Start Docker Desktop and verify the Home Media stack"
    $shortcut.IconLocation = $icon
    $shortcut.Save()
    Write-Host "Created $destination"
}

Write-Host ""
Write-Host "The desktop and Start Menu shortcuts are ready." -ForegroundColor Green
Write-Host "To keep it on the taskbar, right-click 'Home Media Server' in the Start Menu and choose 'Pin to taskbar'."
