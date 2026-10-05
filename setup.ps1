<#
.SYNOPSIS
    Set up the Sign2Text Python environment with the correct runtime profile.

.DESCRIPTION
    Detects whether an NVIDIA GPU is available and installs the matching
    requirements file (CUDA or CPU).  Creates a .venv if one does not exist.

.PARAMETER Profile
    Force a specific runtime profile instead of auto-detecting.
    Valid values: auto (default), cuda, cpu.

.EXAMPLE
    .\setup.ps1                  # auto-detect GPU
    .\setup.ps1 -Profile cpu     # force CPU-only install
#>

[CmdletBinding()]
param(
    [ValidateSet("auto", "cuda", "cpu")]
    [string]$Profile = "auto"
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot

# == 1. Locate Python ================================================
function Get-PythonProbe {
    param(
        [string]$Command,
        [string[]]$Args
    )
    try {
        $probeArgs = $Args + '--version'
        $output = & $Command @probeArgs 2>&1
        if ($LASTEXITCODE -ne 0) { return $null }
        $output = $output.Trim()
        if ($output -match 'Python\s+(\d+)\.(\d+)\.(\d+)') {
            return [PSCustomObject]@{
                Command = $Command
                Args = $Args
                Major = [int]$matches[1]
                Minor = [int]$matches[2]
                Patch = [int]$matches[3]
                Raw = $output
            }
        }
    } catch {
        return $null
    }
    return $null
}

function Is-SupportedPython {
    param([pscustomobject]$Probe)
    if (-not $Probe) { return $false }
    return ($Probe.Major -gt 3) -or ($Probe.Major -eq 3 -and $Probe.Minor -ge 11)
}

$pythonCandidates = @(
    @{ Command = 'python'; Args = @() },
    @{ Command = 'py'; Args = @('-3.14') },
    @{ Command = 'py'; Args = @() }
)

$selectedPython = $null
foreach ($candidate in $pythonCandidates) {
    if (-not (Get-Command $candidate.Command -ErrorAction SilentlyContinue)) {
        continue
    }
    $probe = Get-PythonProbe $candidate.Command $candidate.Args
    if ($probe -and (Is-SupportedPython $probe)) {
        $selectedPython = $probe
        break
    }
}

if (-not $selectedPython) {
    $pythonInstalled = Get-Command python -ErrorAction SilentlyContinue
    $pyInstalled = Get-Command py -ErrorAction SilentlyContinue

    if (-not $pythonInstalled -and -not $pyInstalled) {
        Write-Error "Python is not found in PATH. Install Python 3.11+ or the Windows Python launcher and try again."
        exit 1
    }

    $detected = $null
    if ($pythonInstalled) {
        $detected = Get-PythonProbe 'python' @()
    }
    if (-not $detected -and $pyInstalled) {
        $detected = Get-PythonProbe 'py' @()
    }

    if ($detected) {
        Write-Error "Detected $($detected.Raw), but Sign2Text requires Python 3.11+." 
    } else {
        Write-Error "A Python interpreter was found, but it is not usable for Sign2Text. Install Python 3.11+ or the Windows Python launcher and try again."
    }
    exit 1
}

Write-Host "Using Python: $($selectedPython.Raw) via $($selectedPython.Command) $($selectedPython.Args -join ' ')" -ForegroundColor Cyan

# == 2. Create virtual environment if needed =========================
$venvPath = Join-Path $Root ".venv"
$venvPython = Join-Path $venvPath "Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    Write-Host "Creating virtual environment at $venvPath ..." -ForegroundColor Yellow
    & $selectedPython.Command @($selectedPython.Args + @('-m', 'venv', $venvPath))
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Failed to create virtual environment using $($selectedPython.Command)."
        exit 1
    }
    Write-Host "Virtual environment created." -ForegroundColor Green
} else {
    Write-Host "Virtual environment already exists." -ForegroundColor Green
}

# == 3. Activate =====================================================
& "$venvPath\Scripts\Activate.ps1"

# == 4. Resolve runtime profile ======================================
$resolvedProfile = $Profile

if ($Profile -eq "auto") {
    Write-Host "Detecting GPU ..." -ForegroundColor Cyan
    try {
        $null = & nvidia-smi 2>$null
        if ($LASTEXITCODE -eq 0) {
            $resolvedProfile = "cuda"
            Write-Host "  NVIDIA GPU detected -- selecting CUDA profile." -ForegroundColor Green
        } else {
            $resolvedProfile = "cpu"
            Write-Host "  nvidia-smi failed -- selecting CPU profile." -ForegroundColor Yellow
        }
    } catch {
        $resolvedProfile = "cpu"
        Write-Host "  nvidia-smi not found -- selecting CPU profile." -ForegroundColor Yellow
    }
}

# == 5. Install dependencies =========================================
if ($resolvedProfile -eq "cuda") {
    $reqFile = "requirements.runtime.txt"
} else {
    $reqFile = "requirements.runtime-cpu.txt"
}

# 5a. Build the onnxruntime-meta wheel for the CUDA profile.
# requirements.runtime.txt references the wheel by relative path, but
# packaging/onnxruntime-meta/dist/ is gitignored, so on a fresh clone the
# wheel does not exist yet. Build it on demand.
if ($resolvedProfile -eq "cuda") {
    $metaDir = Join-Path $Root "packaging\onnxruntime-meta"
    $metaDistDir = Join-Path $metaDir "dist"
    $metaWheel = Join-Path $metaDistDir "onnxruntime-1.24.2-py3-none-any.whl"
    if (-not (Test-Path $metaWheel)) {
        Write-Host ""
        Write-Host "Building onnxruntime-meta wheel (rtmlib compatibility shim) ..." -ForegroundColor Cyan
        if (-not (Test-Path $metaDistDir)) {
            New-Item -ItemType Directory -Path $metaDistDir | Out-Null
        }
        & $venvPython -m pip wheel --no-deps -w $metaDistDir $metaDir
        if ($LASTEXITCODE -ne 0) {
            Write-Error "Failed to build onnxruntime-meta wheel."
            exit 1
        }
    }
}

$reqPath = Join-Path $Root $reqFile
Write-Host ""
Write-Host "Installing $reqFile ($resolvedProfile profile) ..." -ForegroundColor Cyan
& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to upgrade pip in the virtual environment."
    exit 1
}
& $venvPython -m pip install -r $reqPath
if ($LASTEXITCODE -ne 0) {
    Write-Error "pip install failed. Check the output above."
    exit 1
}

# == 6. Build landing page (optional; graceful skip if npm unavailable) ==
$landingDir = Join-Path $Root "landing"
$landingPkg = Join-Path $landingDir "package.json"
$landingDist = Join-Path $landingDir "dist\index.html"
if (Test-Path $landingPkg) {
    Write-Host ""
    Write-Host "Building landing page ..." -ForegroundColor Cyan
    $npmCmd = Get-Command npm -ErrorAction SilentlyContinue
    if (-not $npmCmd) {
        Write-Host "  npm not found on PATH - skipping landing build." -ForegroundColor Yellow
        Write-Host "  Install Node.js 18+ from https://nodejs.org/ and re-run setup to build the landing page." -ForegroundColor Yellow
    } else {
        Push-Location $landingDir
        try {
            $nodeModules = Join-Path $landingDir "node_modules"
            if (-not (Test-Path $nodeModules)) {
                Write-Host "  Running npm install ..." -ForegroundColor Cyan
                & npm install --no-audit --no-fund
                if ($LASTEXITCODE -ne 0) {
                    Write-Host "  npm install failed - skipping landing build." -ForegroundColor Yellow
                }
            }
            if ($LASTEXITCODE -eq 0) {
                Write-Host "  Running npm run build ..." -ForegroundColor Cyan
                & npm run build
                if ($LASTEXITCODE -eq 0 -and (Test-Path $landingDist)) {
                    Write-Host "  Landing built: landing\dist\index.html" -ForegroundColor Green
                } else {
                    Write-Host "  Landing build did not produce dist\index.html." -ForegroundColor Yellow
                }
            }
        } finally {
            Pop-Location
            $global:LASTEXITCODE = 0
        }
    }
}

# == 7. Summary ======================================================
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Setup complete: $resolvedProfile profile" -ForegroundColor Green
Write-Host ""
Write-Host "  To start the application:" -ForegroundColor Green
Write-Host "    .\start.ps1" -ForegroundColor White
Write-Host ""
Write-Host "  To force a specific profile:" -ForegroundColor Green
Write-Host "    .\start.ps1 -Profile $resolvedProfile" -ForegroundColor White
Write-Host "========================================" -ForegroundColor Green
