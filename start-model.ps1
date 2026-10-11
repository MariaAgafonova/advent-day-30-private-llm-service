$ErrorActionPreference = 'Stop'
# Existing installation and weights; no download, global changes or port exposure.
$day30Ollama = 'F:\LocalLLM\Ollama\ollama.exe'
if (-not (Test-Path -LiteralPath $day30Ollama)) { throw 'Не найден F:\LocalLLM\Ollama\ollama.exe' }
try {
    $null = Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/version' -TimeoutSec 3
    Write-Host 'Ollama уже работает. Используйте start-local.ps1.'
    exit 0
} catch { }
$env:OLLAMA_HOST = '127.0.0.1:11434'
$env:OLLAMA_MODELS = 'F:\LocalLLM\models'
$env:OLLAMA_NO_CLOUD = '1'
$env:OLLAMA_NUM_PARALLEL = '1'
$env:OLLAMA_MAX_LOADED_MODELS = '1'
$env:OLLAMA_CONTEXT_LENGTH = '8192'
$env:OLLAMA_DEBUG_LOG_REQUESTS = 'false'
$env:OLLAMA_NOPRUNE = '1'
Write-Host 'Запуск установленного Ollama только на loopback. Остановка: Ctrl+C.'
& $day30Ollama serve
exit $LASTEXITCODE
