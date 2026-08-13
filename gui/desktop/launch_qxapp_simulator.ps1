param(
    [string]$Url = "http://127.0.0.1:8000/?capture"
)

$ErrorActionPreference = "Stop"
$DesktopDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $env:LOCALAPPDATA "QxAppDesktop\venv\Scripts\pythonw.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Q-xApp desktop environment is missing. Install gui/desktop/requirements.txt first."
}

Start-Process -FilePath $Python -ArgumentList @(
    (Join-Path $DesktopDir "qxapp_simulator.py"),
    "--url",
    $Url
)
