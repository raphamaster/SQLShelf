# build_app.ps1 — Build SQLShelf Windows distributable via PyInstaller.
# Usage: .\build_app.ps1

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Write-Host "=== SQLShelf — PyInstaller build ===" -ForegroundColor Cyan

# Activate venv if present
if (Test-Path "venv\Scripts\Activate.ps1") {
    . "venv\Scripts\Activate.ps1"
    Write-Host "Activated venv" -ForegroundColor Green
}

# Ensure PyInstaller is installed
python -m pip install --quiet pyinstaller

# Clean previous build artefacts
if (Test-Path "dist") { Remove-Item -Recurse -Force "dist" }
if (Test-Path "build") { Remove-Item -Recurse -Force "build" }

# Run PyInstaller
Write-Host "Running PyInstaller…" -ForegroundColor Cyan
pyinstaller sqlshelf.spec --noconfirm

if ($LASTEXITCODE -ne 0) {
    Write-Error "PyInstaller failed (exit $LASTEXITCODE)"
    exit $LASTEXITCODE
}

Write-Host ""
Write-Host "Build complete — output: dist\SQLShelf\" -ForegroundColor Green
Write-Host "Run: dist\SQLShelf\SQLShelf.exe"
