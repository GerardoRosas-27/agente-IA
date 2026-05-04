$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
python -m pytest tests/ -q --tb=no
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "init OK (pytest verde)"
