$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONUTF8 = '1'
$day30Base = 'D:\AIAdvent\advent-day-29-optimized-local-rag\.venv'
$day30BasePython = Join-Path $day30Base 'Scripts\python.exe'
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    if (Test-Path -LiteralPath $day30BasePython) { & $day30BasePython -B -m venv .venv }
    else { py -3.11 -m venv .venv }
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось создать Python venv. Нужен Python 3.11.' }
}
$day30Pth = Join-Path $PSScriptRoot '.venv\Lib\site-packages\day29_dependencies.pth'
if (Test-Path -LiteralPath $day30Pth) {
    Remove-Item -LiteralPath $day30Pth -Force
}
$day30Requirements = if (Test-Path -LiteralPath 'requirements-lock.txt') { 'requirements-lock.txt' } else { 'requirements.txt' }
& .\.venv\Scripts\python.exe -B -m pip install -r $day30Requirements --extra-index-url https://download.pytorch.org/whl/cpu
if ($LASTEXITCODE -ne 0) { throw 'Не удалось установить зависимости.' }
if (-not (Test-Path -LiteralPath '.env')) {
    & .\.venv\Scripts\python.exe -B scripts\init_config.py
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось создать .env.' }
}
Write-Host 'Готово. Ключ находится в .env (SERVICE_API_KEY). Локальный запуск: .\start-local.ps1'
