param([string]$ISCCPath = "")
$ErrorActionPreference = 'Stop'
$workspaceRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $workspaceRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Create .venv and install requirements-build.lock, then pip install --no-deps -e . first. See README.'
}
Push-Location $workspaceRoot
try {
    & $pythonPath (Join-Path $PSScriptRoot 'build_release.py') "--iscc=$ISCCPath"
    if ($LASTEXITCODE -ne 0) { throw "Release build failed (exit $LASTEXITCODE)." }
} finally {
    Pop-Location
}
