$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'scripts\start-service.ps1') -Mode local
exit $LASTEXITCODE
