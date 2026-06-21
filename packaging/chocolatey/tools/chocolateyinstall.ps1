# Chocolatey Install Script for Empirica
# Documentation: https://docs.chocolatey.org/en-us/create/create-packages

$ErrorActionPreference = 'Stop'

$packageName = 'empirica'
$packageVersion = '1.12.3'
$url = "https://files.pythonhosted.org/packages/source/e/empirica/empirica-$packageVersion.tar.gz"
$checksum = '880c80bcf530d144b29308520694ae0a6f96986f8bc81a6ab16b6ea9c1dfd323'  # TODO: Update sha256 after PyPI publish
$checksumType = 'sha256'

Write-Host "Installing Empirica $packageVersion..." -ForegroundColor Cyan

# Check if Python is installed
$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) {
    Write-Error "Python is not installed or not in PATH. Please install Python 3.11+ first."
    throw "Python 3.11+ is required"
}

# Verify Python version
$pythonVersion = & python --version 2>&1
Write-Host "Found: $pythonVersion" -ForegroundColor Green

if ($pythonVersion -notmatch 'Python 3\.(1[1-9]|[2-9]\d)') {
    Write-Warning "Python 3.11+ is recommended. Found: $pythonVersion"
}

# Install via pip
Write-Host "Installing Empirica via pip..." -ForegroundColor Cyan
$pipArgs = @(
    '-m', 'pip',
    'install',
    '--upgrade',
    "empirica==$packageVersion"
)

$exitCode = Start-ChocolateyProcessAsAdmin `
    -Statements ($pipArgs -join ' ') `
    -ExeToRun 'python' `
    -ValidExitCodes @(0) `
    -WorkingDirectory $env:TEMP

if ($exitCode -eq 0) {
    Write-Host "Empirica installed successfully!" -ForegroundColor Green
    Write-Host ""
    Write-Host "Quick Start:" -ForegroundColor Cyan
    Write-Host "  empirica bootstrap --ai-id myagent --level extended"
    Write-Host "  empirica --help"
    Write-Host ""
    Write-Host "Documentation: https://github.com/nubaeon/empirica" -ForegroundColor Cyan
} else {
    throw "Installation failed with exit code: $exitCode"
}
