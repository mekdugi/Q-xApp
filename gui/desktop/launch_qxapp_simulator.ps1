param(
    [string]$Url = "http://127.0.0.1:8000/?capture",
    [string]$WslDistro = "Ubuntu",
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$HostData
)

$ErrorActionPreference = "Stop"
$DesktopDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $env:LOCALAPPDATA "QxAppDesktop\venv\Scripts\pythonw.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Q-xApp desktop environment is missing. Install gui/desktop/requirements.txt first."
}

$Arguments = @(
    (Join-Path $DesktopDir "qxapp_simulator.py"),
    "--url",
    $Url,
    "--wsl-distro",
    $WslDistro,
    "--host-data",
    $HostData
)

Start-Process -FilePath $Python -ArgumentList $Arguments
