"""Create a local ignored secret without printing it or overwriting existing settings."""
from pathlib import Path
import secrets
import sys

sys.stdout.reconfigure(encoding='utf-8')

root = Path(__file__).resolve().parents[1]
path = root / '.env'
if path.exists():
    print('.env уже существует; изменений нет.')
else:
    text = (root / '.env.example').read_text(encoding='utf-8')
    text = text.replace('SERVICE_API_KEY=\n', 'SERVICE_API_KEY=' + secrets.token_urlsafe(36) + '\n', 1)
    with path.open('x', encoding='utf-8', newline='\n') as f:
        f.write(text)
    print('Создан .env со случайным API-ключом. Секрет не выводится в консоль.')
