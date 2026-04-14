param(
    [ValidateSet("auto", "cpu", "cu130")]
    [string]$Profile = "auto",

    [ValidateSet("check-only", "auto-install")]
    [string]$PythonMode = "check-only",

    [string]$Venv = ".venv",

    [switch]$NonInteractive
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Installer = Join-Path $ScriptDir "scripts\install_env.py"

if (-not (Test-Path $Installer)) {
    throw "Installer script not found: $Installer"
}

$python = if (Get-Command py -ErrorAction SilentlyContinue) {
    "py"
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    "python"
} else {
    throw "Python launcher not found. Install Python 3.11 first or use -PythonMode auto-install."
}

$argsList = @()
if ($python -eq "py") {
    $argsList += "-3"
}
$argsList += @($Installer, "--profile", $Profile, "--python-mode", $PythonMode, "--venv", $Venv)
if ($NonInteractive) {
    $argsList += "--non-interactive"
}

& $python @argsList
exit $LASTEXITCODE
