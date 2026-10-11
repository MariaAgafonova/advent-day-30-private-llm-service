"""Fault injection with REAL Ollama; isolated database, real timeout and TCP refusal."""
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import secrets
import sys
import time
import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from private_llm.config import Config
from private_llm.app import create_app


async def main():
    cfg = replace(Config.load(), keys=(secrets.token_urlsafe(36),), rag_enabled=False,
                  db_path=ROOT / 'data/recovery.sqlite', log_path=ROOT / 'logs/recovery.jsonl',
                  generation_timeout=.05, rate_limit=100)
    app = create_app(cfg)
    auth = {'Authorization': 'Bearer ' + cfg.keys[0]}
    report = {'kind': 'in-process HTTP routes with REAL Ollama transport; injected deadlines and dead TCP port'}
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as c:
            async def request(label, prompt):
                tick = time.perf_counter()
                response = await c.post('/api/chat', headers=auth, json={'message': prompt})
                report[label] = {'status': response.status_code, 'seconds': time.perf_counter()-tick, **response.json()}
                print(label, response.status_code, flush=True)
                return response
            first = await request('forced_timeout', 'Напиши большой рассказ о море.')
            assert first.status_code == 504
            assert app.state.queue.status()['active'] == 0
            cfg.generation_timeout = 120
            second = await request('after_timeout', 'Ответь только: ВОССТАНОВЛЕНО-30')
            assert second.status_code == 200 and 'ВОССТАНОВЛЕНО-30' in second.json()['answer']
            actual_url = app.state.runtime.client.base_url
            # Reserve an unused local TCP port without listening: connection must be refused.
            import socket
            with socket.socket() as bound:
                bound.bind(('127.0.0.1', 0))
                port = bound.getsockname()[1]
                app.state.runtime.client.base_url = httpx.URL(f'http://127.0.0.1:{port}')
                dead = await request('unavailable_runtime', 'Недоступная модель')
                assert dead.status_code == 503
            app.state.runtime.client.base_url = actual_url
            restored = await request('after_runtime_restore', 'Ответь только: СНОВА-РАБОТАЕТ-30')
            assert restored.status_code == 200 and 'СНОВА-РАБОТАЕТ-30' in restored.json()['answer']
    (ROOT / 'reports/recovery-verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    asyncio.run(main())
