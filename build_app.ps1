# build_app.ps1 - Build SQLShelf Windows distributable via PyInstaller.
# Usage: .\build_app.ps1

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# Run a native command and fail on its exit code, not on its stderr.
# Windows PowerShell 5.1 turns every stderr line (pip notices, PyInstaller's
# INFO log) into an error record, which "Stop" would treat as fatal.
function Invoke-Native {
    param([Parameter(Mandatory)][scriptblock]$Command)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $Command 2>&1 | ForEach-Object { "$_" }
    } finally {
        $ErrorActionPreference = $previous
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed (exit $LASTEXITCODE): $Command"
    }
}

Write-Host "=== SQLShelf - PyInstaller build ===" -ForegroundColor Cyan

# Activate venv if present
if (Test-Path "venv\Scripts\Activate.ps1") {
    . "venv\Scripts\Activate.ps1"
    Write-Host "Activated venv" -ForegroundColor Green
}

# Ensure PyInstaller is installed
Invoke-Native { python -m pip install --quiet --disable-pip-version-check pyinstaller }

# Clean previous build artefacts
if (Test-Path "dist") { Remove-Item -Recurse -Force "dist" }
if (Test-Path "build") { Remove-Item -Recurse -Force "build" }

# Run PyInstaller
Write-Host "Running PyInstaller..." -ForegroundColor Cyan
Invoke-Native { pyinstaller sqlshelf.spec --noconfirm }

# Create portable zip, named like the release asset
$version = (Select-String -Path "sqlshelf\__init__.py" -Pattern '__version__ = "(.+)"').Matches[0].Groups[1].Value
Write-Host "Creating portable zip..." -ForegroundColor Cyan
$portableOut = "dist\SQLShelf-$version-windows-x64-portable.zip"
Compress-Archive -Path "dist\SQLShelf\*" -DestinationPath $portableOut

Write-Host ""
Write-Host "Build complete." -ForegroundColor Green
Write-Host "  Folder  : dist\SQLShelf\"
Write-Host "  Portable: $portableOut"
Write-Host "  Run     : dist\SQLShelf\SQLShelf.exe"
