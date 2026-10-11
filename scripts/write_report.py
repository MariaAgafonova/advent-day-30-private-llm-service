"""Build REPORT.md from saved measurements, never from expected timings."""
from datetime import datetime, timezone
import json
from pathlib import Path
import xml.etree.ElementTree as ET

root = Path(__file__).resolve().parents[1]

def read(name):
    return json.loads((root / 'reports' / name).read_text(encoding='utf-8-sig'))

live = read('live-verification.json')
recovery = read('recovery-verification.json')
preservation = read('source-preservation.json')
network = read('network-verification.json')
suite = ET.parse(root / 'reports/tests.xml').getroot().find('testsuite')
model = live['status_after']['runtime']
changed = [p for p in preservation['before']['sha256_files']
           if preservation['before']['sha256_files'][p] != preservation['after']['sha256_files'].get(p)]
concurrent = sorted(live['concurrent'], key=lambda r: r['metrics']['queue_ms'])
lines = [
    '# Отчёт: День 30. Локальная LLM как приватный сервис', '',
    'Дата формирования (UTC): ' + datetime.now(timezone.utc).isoformat(timespec='seconds'), '',
    '## Результат', '',
    'Проект создан в `D:\\AIAdvent\\advent-day-30-private-llm-service`. API, потоковая генерация, локальная история, RAG и очередь проверены на реальной Ollama. Windows-скрипты локального и LAN-запуска проверены. Второе устройство и визуальный браузерный прогон недоступны: они явно остаются ручными проверками.', '',
    '## Модель и исходная основа', '',
    f"- Модель: `{model['model']}`; квантование `{model['quantization']}`.",
    f"- Digest: `{model['digest']}`.",
    '- Runtime: установленный Ollama 0.40.0; `F:\\LocalLLM\\Ollama\\ollama.exe`, существующие веса `F:\\LocalLLM\\models`.',
    '- Runtime endpoint: `http://127.0.0.1:11434`; listener проверен на loopback.',
    f"- Фактическое окно из `/api/ps`: **{model['context_length']}**. `num_ctx` передаётся в каждом запросе и проверяется по runtime.",
    '- Основа: день 29 (Python/Streamlit, локальный Ollama chat/streaming, E5 retrieval, SQLite, Qwen tokenizer). AGENTS.md в исходных проектах и их родительской папке не найден.',
    '- Переиспользованы модули models/storage/embeddings/tokenization/search, чтение индекса в SQLite mode=ro, профиль sampling и подход к локальному токенизатору. Структурированный prompt дня 29 адаптирован для потокового текстового чата.',
    '- Добавлен FastAPI для HTTP API и отдельный HTML/CSS/JS интерфейс; исходный Streamlit не предоставляет нужную многопользовательскую API-границу. История создана отдельно: прежнее хранилище не изолировало пользователей по ключу.',
    '- Индекс и токенизатор подключены по путям. Веса и большие индексы не копировались. Облачные LLM и внешние API генерации отсутствуют.', '',
    '## Схема', '',
    '```mermaid',
    'flowchart LR',
    '  UI["Русский веб-чат / HTTP-клиент"] -->|"Bearer API key"| API["FastAPI :8030"]',
    '  API --> AUTH["Авторизация, размер запроса, rate limit"]',
    '  AUTH --> QUEUE["FIFO: 1 активный + 5 ожидающих"]',
    '  QUEUE --> CTX["Qwen tokenizer + бюджет prompt"]',
    '  DB[("SQLite сессий дня 30")] <--> QUEUE',
    '  RAG["Локальный E5 + индекс только чтение"] --> CTX',
    '  CTX -->|"loopback, streaming"| MODEL["Ollama qwen3.5:4b :11434"]',
    '  MODEL -->|"NDJSON + фактические метрики"| API',
    '```', '',
    '## Конфигурация и защита', '',
    '- По умолчанию HOST=127.0.0.1, PORT=8030. Для домашней сети отдельный start-lan.ps1 с явно заданным HOST.',
    '- API и история защищены ключом из .env/окружения. Один ключ — один пользователь; дополнительные ключи разделяют историю и rate limit. Ключ не хранится браузером постоянно и не отправляется в URL.',
    '- 10 генераций/60 секунд/ключ; 1 одновременно; 5 в ожидании; генерация до 120 секунд; ожидание до 600 секунд; тело запроса до 65536 байт; ответ до 512 токенов.',
    '- Контекст 8192, RAG до 2000 токенов/3 источников, до 100 сообщений истории для подготовки prompt. При переполнении удаляются старые полные пары истории и сокращаются цитаты; системные правила и последнее сообщение сохраняются.',
    '- Текст считается локальным Qwen tokenizer, служебная обвязка имеет запас 256 + 24/сообщение. Это консервативный бюджет, а не точное воспроизведение chat template. При отсутствии tokenizer — оценка по UTF-8 байтам.',
    '- Sampling: temperature 0, top_p 0.95, top_k 20, presence_penalty 1.5, seed 28, num_gpu 0, num_thread 4, think=false. Сохранён профиль дня 29, output уменьшен с 768 до 512.',
    '- Журнал JSONL содержит request_id, время, HTTP/итоговый статус, длину очереди и тип ошибки; тексты и заголовки авторизации не записываются. SQLite/.env/логи исключены из Git.',
    '- CORS по умолчанию same-origin; wildcard запрещён. HTML модели выводится текстовыми узлами, безопасное подмножество Markdown проверено тестом. CSP запрещает inline scripts и внешние ресурсы.',
    '- Firewall, VPN, маршрутизация и роутер не менялись. Инструкции: README.md и VPN.md.', '',
    '## Выполненные проверки', '',
    f"- Python: **{suite.attrib['tests']} тестов**, failures={suite.attrib['failures']}, errors={suite.attrib['errors']}; {suite.attrib['time']} с. Файл: reports/tests.xml.",
    '- Эти тесты используют mock для проверки логики: 401, изоляция пользователей/сессий, сохранение/повторное открытие SQLite, 400/413/422, chunked limit, 429 + Retry-After (точный лимит 10), 503 при 1+5 и истечении ожидания, 504 и освобождение очереди, отмена, CORS, stream errors, remote-model rejection и неподтверждённое окно.',
    '- JavaScript: node --check web/app.js; node tests/test_web_safety.cjs. Проверены безопасные текстовые узлы, Markdown, запрет javascript: в источниках и отсутствие постоянного хранения ключа. Это проверка логики, не визуальный тест браузера.',
    '- pip check: No broken requirements found. Точные версии зафиксированы в requirements-lock.txt.',
    f"- Реальный HTTP + Ollama: **{sum(live['checks'].values())}/{len(live['checks'])} проверок** успешно; весь сценарий {live['elapsed_seconds']:.3f} с. Файл: reports/live-verification.json.",
    '- Настоящие /health, авторизация, статические файлы чата, история, независимые сессии, RAG с цитатами, поток NDJSON, фактический token budget и очередь проверены через HTTP-сокет.', '',
    '## Пять одновременных запросов', '',
    'Запросы запускались одновременно. Порядок ниже — фактическое поступление в очередь, а не номер задачи клиента. У всех HTTP 200; все пять маркеров в ответах точно совпали со своими запросами; у всех разные request_id и session_id. Одновременно наблюдалась одна активная генерация и до четырёх ожидающих.', '',
    '| Маркер / ответ | Время клиента, с | Очередь, с | Обработка, с | Вход / выход, токенов |',
    '|---|---:|---:|---:|---:|'
]
for item in concurrent:
    m = item['metrics']
    lines.append(f"| {item['marker']} | {item['wall_seconds']:.3f} | {m['queue_ms']/1000:.3f} | {m['generation_ms']/1000:.3f} | {m['prompt_eval_count']} / {m['eval_count']} |")
lines += ['', 'Все ответы сверены с историей соответствующей сессии. Очередь приложения опустела; процесс продолжил принимать запросы. Лимит именно пяти ожидающих и отказ шестому дополнительно проверен детерминированным тестом.', '',
          '## Другие измерения и восстановление', '',
          '| Сценарий | Результат | Измеренное время, с |', '|---|---|---:|']
for name, item in zip(['Первый ответ', 'Ответ с памятью', 'RAG с источниками'], live['requests'][:3]):
    lines.append(f"| {name} | HTTP {item['http_status']} | {item['wall_seconds']:.3f} |")
lines.append(f"| Реальный stream: первый фрагмент / весь запрос | done, текст совпал с суммой delta | {live['stream']['first_delta_seconds']:.3f} / {live['stream']['total_seconds']:.3f} |")
for name in ('forced_timeout', 'after_timeout', 'unavailable_runtime', 'after_runtime_restore'):
    item = recovery[name]
    lines.append(f"| {name} | HTTP {item['status']} | {item['seconds']:.3f} |")
lines += ['',
    'Проверка восстановления использовала реальные соединения с Ollama и отдельную БД: timeout принудительно уменьшен до 0,05 с, затем возвращён к 120 с. Для отказа runtime выбран реальный локальный TCP-порт без listener, после чего восстановлен исходный URL. Mock LLM здесь не использовалась. Обработчики API проверялись в процессе теста; полный socket HTTP проверен основным сценарием. Файл: reports/recovery-verification.json.', '',
    '## Windows и сеть', '',
    f"- Локальный listener: {network['local_listener']}; /health после повторного запуска: {network['local_health']}.",
    f"- LAN listener: {network['lan_listener']}; /health с того же ПК: {network['lan_health']}.",
    f"- Ollama listener: {network['runtime_listener']}.",
    f"- Сохранённая история доступна через API после перезапуска процесса: {network.get('history_after_restart', False)}.",
    '- Подтверждено только переключение bind-адреса приложения. Запрос с того же компьютера не считается проверкой доступа другого устройства.', '',
    '## Обнаруженные проблемы и исправления', '',
    '- Windows cp1252 не позволяла печатать русские сообщения setup: исправлено явным UTF-8.',
    '- Разбор TEMPERATURE=0.0 выявил неверный исходный тип значения по умолчанию: исправлен и покрыт тестом чтения .env.example.',
    '- Свежая CPU-сборка PyTorch 2.14.1 не загружала c10.dll (WinError 1114). Установлена и закреплена рабочая версия дня 29 — 2.6.0+cpu. После исправления настоящий E5 поиск и RAG прошли.',
    '- При частичной загрузке RAG индекс мог ошибочно означать готовность. Публикация готового состояния стала атомарной после загрузки embedding-модели.',
    '- Исправлено освобождение слота при отмене ещё не стартовавшей coroutine. Тест подтверждает восстановление ёмкости очереди.',
    '- Исходный неуспешный RAG-прогон сохранён в reports/initial-rag-failure.json; итоговые измерения приведены из повторного успешного прогона.', '',
    f"- Ответ после принудительного таймаута занял {recovery['after_timeout']['seconds']:.3f} с, хотя завершился успешно. Причина этой задержки отдельно не установлена. Время ответа может заметно меняться; очередь приложения не управляет другими клиентами того же Ollama.", '',
    '## Контроль исходных проектов', '',
    f"Проверено {len(preservation['before']['sha256_files'])} файлов по SHA-256; {len(preservation['before']['model_file_stats'])} файлов модели — по размеру и mtime. Полная проверка неизменности: **{preservation['unchanged']}**.", '']
if changed:
    lines += ['Во время выполнения обнаружены параллельные изменения файлов дня 29, перечисленных ниже. Команды этой реализации не редактировали исходные проекты; обнаруженные изменения не откатывались. Поэтому полную неизменность исходной папки в течение сеанса утверждать нельзя. Скопированные модули retrieval, профиль, индексы и токенизаторы не изменились; размер/mtime файлов весов совпали.', '']
    lines += ['- `' + p + '`' for p in changed]
else:
    lines += ['Контрольные суммы проверенных исходных файлов совпали.']
lines += ['', 'Полный снимок сравнения: reports/source-preservation.json. В отчёте содержатся только хеши .env, не его значения.', '',
    '## Что требует ручной проверки', '',
    '- Встроенный браузер в этой сессии недоступен (список браузеров пуст). Визуальное отображение и клики на компьютере/телефоне не выдаются за выполненные проверки. HTTP assets, JavaScript syntax и безопасный renderer проверены автоматически.',
    '- Телефон в той же сети: открыть LAN-IP компьютера, проверить неверный/верный ключ, поток ответа, RAG-источники, новую и старую сессию, узкий экран и сохранение ввода при ошибке.',
    '- Перезагрузка страницы должна очистить ключ, после повторного ввода история снова доступна.',
    '- Доступ по мобильному интернету через самостоятельно настроенную VPN; вне VPN сервис недоступен. Firewall и изоляцию Wi-Fi проверить на реальных устройствах.',
    '- Подробный сценарий: README.md, раздел «Ручная проверка с телефона».', '']
(root / 'REPORT.md').write_text('\n'.join(lines), encoding='utf-8')
print('REPORT.md built from recorded measurements.')
