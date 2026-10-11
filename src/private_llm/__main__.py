import argparse
import asyncio
import ipaddress
import sys
from .config import Config
from .runtime import Runtime
from .errors import ServiceError


async def check(config):
    runtime = Runtime(config)
    try:
        model = await runtime.status()
        print(f"Ollama: {model['model']}; окно профиля: {config.context_window}; загружено: {model['context_length']}")
        if config.rag_enabled:
            if not config.index_path.is_file():
                raise ValueError('LOCAL_INDEX_PATH не найден; исправьте путь или задайте RAG_ENABLED=false')
        if not config.tokenizer_path.is_file():
            print('Токенизатор не найден: будет использована консервативная оценка по UTF-8 байтам.')
    finally:
        await runtime.close()


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['local', 'lan'], default='local')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--runtime-port', action='store_true')
    args = parser.parse_args()
    try:
        config = Config.load()
        if args.runtime_port:
            from urllib.parse import urlsplit
            print(urlsplit(config.runtime_url).port or 80)
            return 0
        if args.mode == 'local':
            config.host = '127.0.0.1'
        elif config.host in ('127.0.0.1', 'localhost', '::1'):
            raise ValueError('Для LAN задайте HOST=0.0.0.0 или LAN/VPN IP в .env')
        asyncio.run(check(config))
        if args.check:
            return 0
        from .app import create_app
        import uvicorn
        address = '127.0.0.1' if config.host in ('0.0.0.0', '::') else config.host
        if ':' in address:
            address = f'[{address}]'
        print(f'Веб-чат: http://{address}:{config.port}', flush=True)
        if args.mode == 'lan':
            print(f'С телефона: http://<LAN-IP-компьютера>:{config.port}. Firewall настройте вручную.', flush=True)
        print('Остановка: Ctrl+C. Модель и история остаются на этом компьютере.', flush=True)
        uvicorn.run(create_app(config), host=config.host, port=config.port, workers=1,
                    access_log=False, log_level='critical', proxy_headers=False,
                    timeout_graceful_shutdown=3, timeout_keep_alive=5)
        return 0
    except ServiceError as exc:
        print('Ошибка запуска: ' + exc.message, file=sys.stderr)
    except (ValueError, OSError) as exc:
        print('Ошибка запуска: ' + str(exc), file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
