[CmdletBinding()]
param(
    [ValidateSet("auto", "cuda", "cpu")]
    [string]$Profile = "auto",

    [switch]$Tunnel,

    [Alias("no-ollama")]
    [switch]$NoOllama
)

$ErrorActionPreference = "Stop"

# Propagate the chosen runtime profile to the Python runtime.
$env:ASL_RUNTIME_PROFILE = $Profile

# When --NoOllama is set, tell the ASL backend to skip Ollama translation
# so raw glosses (e.g. "HELLO HOW YOU") appear directly in captions.
if ($NoOllama) {
    $env:ASL_DISABLE_TRANSLATION = "1"
} else {
    $env:ASL_DISABLE_TRANSLATION = "0"
}

# & "$PSScriptRoot\.venv\Scripts\Activate.ps1"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  ASL Runtime Profile: $Profile" -ForegroundColor Cyan
if ($Tunnel) {
    Write-Host "  Tunnel Mode:         ENABLED" -ForegroundColor Magenta
}
if ($NoOllama) {
    Write-Host "  Ollama Translation:  DISABLED (raw gloss mode)" -ForegroundColor Yellow
}
Write-Host "========================================" -ForegroundColor Cyan

$pyArgs = @("$PSScriptRoot\start.py")
if ($Tunnel) { $pyArgs += "--tunnel" }

$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Error "Project virtual environment not found. Run .\setup.ps1 first."
    exit 1
}

& $venvPython @pyArgs