param([ValidateSet('local','lan')][string]$Mode = 'local')
$ErrorActionPreference = 'Stop'
$day30Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $day30Root
try {
    if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) { throw 'Сначала выполните .\setup.ps1' }
    if (-not (Test-Path -LiteralPath '.env') -and -not $env:SERVICE_API_KEY) { throw 'Создайте .env с ключом или выполните setup.ps1.' }
    $env:PYTHONPATH = Join-Path $day30Root 'src'
    $env:PYTHONDONTWRITEBYTECODE = '1'
    $env:PYTHONUTF8 = '1'
    $env:HF_HUB_OFFLINE = '1'
    $env:TRANSFORMERS_OFFLINE = '1'
    # Refuse an Ollama listener exposed to the network. Never change its settings here.
    $day30RuntimePort = & .\.venv\Scripts\python.exe -B -m private_llm --runtime-port
    if ($LASTEXITCODE -ne 0) { throw 'Проверьте настройки .env.' }
    $day30Listeners = @(Get-NetTCPConnection -State Listen -LocalPort ([int]$day30RuntimePort) -ErrorAction SilentlyContinue)
    foreach ($day30Listener in $day30Listeners) {
        if ($day30Listener.LocalAddress -notin @('127.0.0.1','::1')) { throw 'Ollama слушает сеть. Перезапустите runtime с OLLAMA_HOST=127.0.0.1:11434.' }
    }
    & .\.venv\Scripts\python.exe -B -m private_llm --mode $Mode
    if ($LASTEXITCODE -ne 0) { throw 'Сервис не запущен. Проверьте сообщение выше и доступность Ollama.' }
} catch {
    Write-Host ('Ошибка: ' + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
