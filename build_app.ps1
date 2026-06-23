# build_app.ps1 - Build SQLShelf Windows distributable via PyInstaller.
# Usage: .\build_app.ps1

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Write-Host "=== SQLShelf - PyInstaller build ===" -ForegroundColor Cyan

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
Write-Host "Running PyInstaller..." -ForegroundColor Cyan
pyinstaller sqlshelf.spec --noconfirm

if ($LASTEXITCODE -ne 0) {
    Write-Error "PyInstaller failed (exit $LASTEXITCODE)"
    exit $LASTEXITCODE
}

# Create portable zip
Write-Host "Creating portable zip..." -ForegroundColor Cyan
$portableOut = "dist\SQLShelf-portable-windows-x64.zip"
if (Test-Path $portableOut) { Remove-Item $portableOut }
Compress-Archive -Path "dist\SQLShelf\*" -DestinationPath $portableOut

Write-Host ""
Write-Host "Build complete." -ForegroundColor Green
Write-Host "  Folder  : dist\SQLShelf\"
Write-Host "  Portable: $portableOut"
Write-Host "  Run     : dist\SQLShelf\SQLShelf.exe"
