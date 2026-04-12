param(
    [string]$OutputName = "AriumInstaller"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$InstallerScript = Join-Path $Root "scripts\install_env.py"

if (-not (Get-Command pyinstaller -ErrorAction SilentlyContinue)) {
    Write-Host "Installing pyinstaller..."
    python -m pip install pyinstaller
}

pyinstaller --onefile --name $OutputName $InstallerScript
Write-Host "Build complete. Binary: dist\$OutputName.exe"
