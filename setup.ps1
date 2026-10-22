# One-command setup for Live Caption on Windows (Linux/macOS: setup.sh).
# Safe to run again: steps that are already done are skipped.
#
# Run it from PowerShell in the project folder:
#   powershell -ExecutionPolicy Bypass -File setup.ps1 [options]
# The options are the same as setup.sh's (--summary, --no-summary, --summary-only, --no-whisper, --yes,
# --recreate-venv, --no-shortcut) plus the app's own, e.g. --model medium or --summary-model qwen2.5:3b.
# No param() block on purpose: Linux-style options like --model must reach Python unchanged.
$recreate = $false
$shortcut = $true
$pass = @()
foreach ($a in $args) {
    if ($a -in "--recreate-venv", "-RecreateVenv") { $recreate = $true }
    elseif ($a -eq "--no-shortcut") { $shortcut = $false }
    elseif ($a -in "-h", "--help") {
        Get-Content $PSCommandPath -TotalCount 7 | Select-Object -Skip 1 | ForEach-Object { $_ -replace "^# ?", "" }
        exit 0
    }
    else { $pass += $a }
}
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Step($text) { Write-Host "`n==> $text" -ForegroundColor White }
function Note($text) { Write-Host "    $text" }
function Fail($text) { Write-Host "    x $text" -ForegroundColor Red; exit 1 }
function Have($name) { [bool](Get-Command $name -ErrorAction SilentlyContinue) }
# Windows PowerShell doesn't stop when an external program fails: check its exit code.
function Run([scriptblock]$command, [string]$what) {
    & $command
    if ($LASTEXITCODE -ne 0) { Fail "$what failed (exit code $LASTEXITCODE)." }
}
function Add-ToPath($dir) {
    if ((Test-Path $dir) -and -not ($env:Path -split ";" -contains $dir)) { $env:Path = "$dir;$env:Path" }
}
# Installers record new folders in the registry; this window only sees them after reading it again.
function Read-RegistryPath {
    foreach ($scope in "Machine", "User") {
        foreach ($dir in [Environment]::GetEnvironmentVariable("Path", $scope) -split ";") {
            if ($dir) { Add-ToPath ([Environment]::ExpandEnvironmentVariables($dir)) }
        }
    }
    Add-ToPath "$env:USERPROFILE\.local\bin"              # where uv's own installer puts it
    Add-ToPath "$env:LOCALAPPDATA\Microsoft\WinGet\Links"  # where winget links command-line tools
}

$py = ".venv\Scripts\python.exe"

# ---------------------------------------------------------------------------- 1. uv
Step "uv (Python environment manager)"
if (-not (Have "uv") -and (Have "winget")) {
    winget install --id astral-sh.uv -e --accept-source-agreements --accept-package-agreements
    # 0x8A15002B: "no applicable upgrade", i.e. already installed, just not on this window's PATH yet.
    if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne -1978335189) { Note "winget couldn't install uv (exit code $LASTEXITCODE)" }
    Read-RegistryPath
}
if (-not (Have "uv")) {
    Run { powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex" } "Installing uv"
    Read-RegistryPath
}
if (-not (Have "uv")) { Fail "uv was installed but isn't on PATH: open a new PowerShell window and run setup.ps1 again." }

# ---------------------------------------------------------------------------- 2. Python environment
Step "Python environment (.venv)"
if ($recreate -and (Test-Path .venv)) {
    Note "Removing the old .venv"
    Remove-Item -Recurse -Force .venv
}
if (Test-Path $py) {
    Note "Using the existing .venv ($(& $py --version))"
} else {
    # Python 3.12 from uv: on Windows its Tkinter uses the system fonts, so no system Python is needed.
    Run { uv venv --python 3.12 .venv } "Creating the virtualenv"
}
Note "Installing the Python libraries (first time: about 1-2 GB)..."
Run { uv pip install --python $py -r requirements.txt } "Installing the Python libraries"

# ---------------------------------------------------------------------------- 3. Start menu shortcut
Step "Start menu shortcut"
if ($shortcut) {
    & $py -m lib.shortcut
    if ($LASTEXITCODE -ne 0) { Note "No shortcut; start the app with run.bat instead." }
} else {
    Note "Skipped (--no-shortcut)"
}

# ---------------------------------------------------------------------------- 4. models
# Whisper and the summary model (Ollama is installed there with winget when it's missing), with checks and retries.
$env:PYTHONUTF8 = "1"  # the result list uses check marks, which old console code pages lack
& $py -m lib.model_setup @pass
exit $LASTEXITCODE
