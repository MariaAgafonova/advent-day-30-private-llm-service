"""Real HTTP + real Ollama verification. Test texts/results are intentionally saved as evidence."""
import asyncio
import json
from pathlib import Path
import time
import sys
import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from private_llm.config import Config


async def main():
    cfg = Config.load()
    url = f'http://127.0.0.1:{cfg.port}'
    headers = {'Authorization': 'Bearer ' + cfg.keys[0]}
    report = {'kind': 'real HTTP and real Ollama; no mocks', 'checks': {}, 'requests': [], 'concurrent': []}
    started = time.perf_counter()

    async with httpx.AsyncClient(base_url=url, trust_env=False, timeout=cfg.queue_timeout + cfg.generation_timeout + 15) as c:
        async def check(name, value):
            report['checks'][name] = bool(value)
            print(f'{name}: {bool(value)}', flush=True)
            if not value:
                raise AssertionError(name)

        async def chat(message, **extra):
            tick = time.perf_counter()
            response = await c.post('/api/chat', headers=headers, json={'message': message, **extra})
            data = response.json()
            result = {'message': message, 'http_status': response.status_code,
                      'wall_seconds': round(time.perf_counter()-tick, 3), **data}
            report['requests'].append(result)
            print(json.dumps({'status': response.status_code, 'seconds': result['wall_seconds'],
                              'request_id': data.get('request_id'), 'error': data.get('error')}, ensure_ascii=False), flush=True)
            return result

        try:
            await check('health', (await c.get('/health')).status_code == 200)
            assets = [await c.get(path) for path in ['/', '/app.js', '/style.css']]
            await check('web_assets', all(r.status_code == 200 for r in assets))
            await check('missing_key', (await c.get('/api/status')).status_code == 401)
            await check('wrong_key', (await c.get('/api/sessions', headers={'Authorization': 'Bearer invalid'})).status_code == 401)
            report['status_before'] = (await c.get('/api/status', headers=headers)).json()
            await check('model_available', report['status_before']['runtime']['available'])
            large = await c.post('/api/chat', headers=headers, json={'message': 'x' * 70000})
            await check('body_limit', large.status_code == 413)
            overflow = await c.post('/api/chat', headers=headers, json={'message': ' a' * 12000})
            await check('context_limit', overflow.status_code == 422)

            one = await chat('Запомни код этого диалога: КЕДР-730. Ответь только словом «Запомнил».')
            await check('real_generation', one['http_status'] == 200 and bool(one.get('answer')))
            sid = one['session_id']
            follow = await chat('Какой код я попросил запомнить? Ответь только кодом.', session_id=sid)
            await check('history_used', 'КЕДР-730' in follow.get('answer', ''))
            history = (await c.get(f'/api/sessions/{sid}/messages', headers=headers)).json()
            await check('history_saved', len(history['messages']) == 4)
            await check('history_protected', (await c.get(f'/api/sessions/{sid}/messages')).status_code == 401)

            rag = await chat('When should I use WorkManager for persistent work on Android? Answer briefly with source citations.', use_rag=True)
            await check('rag_sources', rag['http_status'] == 200 and len(rag.get('sources', [])) > 0)
            await check('rag_inline_citations', any('[' + s['citation_id'] + ']' in rag['answer'] for s in rag['sources']))

            async def concurrent(i):
                marker = f'LOCAL30-{i}-OK'
                result = await chat('Ответь ровно этой строкой, без пояснений: ' + marker)
                result['marker'] = marker
                result['matches'] = marker in result.get('answer', '')
                return result
            tasks = [asyncio.create_task(concurrent(i)) for i in range(1, 6)]
            observed = []
            while not all(t.done() for t in tasks):
                state = (await c.get('/api/status', headers=headers)).json()['queue']
                observed.append(state)
                await asyncio.sleep(.25)
            report['concurrent'] = await asyncio.gather(*tasks)
            report['queue_observations'] = observed
            await check('five_concurrent_match', all(r['http_status'] == 200 and r['matches'] for r in report['concurrent']))
            await check('one_active', max(s['active'] for s in observed) == 1)
            await check('bounded_queue', max(s['waiting'] for s in observed) <= 5)
            await check('queue_observed', max(s['waiting'] for s in observed) >= 3)
            await check('distinct_sessions', len({r['session_id'] for r in report['concurrent']}) == 5)
            for r in report['concurrent']:
                hist = (await c.get(f"/api/sessions/{r['session_id']}/messages", headers=headers)).json()['messages']
                await check('isolated_' + r['marker'], len(hist) == 2 and r['marker'] in hist[-1]['content'])

            # Verify incremental NDJSON on a real socket and compare final text to all deltas.
            events, first_delta_at, t0 = [], None, time.perf_counter()
            async with c.stream('POST', '/api/chat', headers=headers,
                                json={'message': 'Ответь только: ПОТОК-30', 'stream': True}) as response:
                async for line in response.aiter_lines():
                    event = json.loads(line)
                    events.append(event)
                    if event['type'] == 'delta' and first_delta_at is None:
                        first_delta_at = time.perf_counter() - t0
            report['stream'] = {'events': events, 'first_delta_seconds': first_delta_at,
                                'total_seconds': time.perf_counter()-t0}
            await check('stream_real', events[-1]['type'] == 'done' and first_delta_at is not None)
            await check('stream_delta_consistency', ''.join(e['text'] for e in events if e['type'] == 'delta') == events[-1]['answer'])
            report['status_after'] = (await c.get('/api/status', headers=headers)).json()
            await check('runtime_applied_context', report['status_after']['runtime']['context_length'] == cfg.context_window)
            await check('actual_token_budget', all(r['metrics']['prompt_eval_count'] + cfg.max_output_tokens <= cfg.context_window for r in report['requests'] if r['http_status'] == 200))
        finally:
            report['elapsed_seconds'] = round(time.perf_counter()-started, 3)
            (ROOT / 'reports/live-verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    asyncio.run(main())
