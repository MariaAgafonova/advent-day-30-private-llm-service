import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import httpx
import pytest

from private_llm.app import create_app
from private_llm.config import Config, ROOT
from private_llm.context import ContextBuilder
from private_llm.errors import ServiceError
from private_llm.queue import GenerationQueue, Job
from private_llm.runtime import Runtime
from private_llm.storage import Store

KEY = 'test-primary-key-' + 'a' * 32
OTHER = 'test-secondary-key-' + 'b' * 32
AUTH = {'Authorization': 'Bearer ' + KEY}


class FakeRuntime:
    def __init__(self):
        self.available, self.delay = True, .01
        self.active, self.max_active = 0, 0
        self.calls, self.cancelled = [], 0
        self.fail = False

    async def status(self):
        if not self.available:
            raise ServiceError(503, 'runtime_unavailable', 'Runtime недоступен.')
        return {'available': True, 'model': 'fake', 'context_length': 8192}

    async def close(self):
        pass

    async def generate(self, messages, emit):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.calls.append(messages)
        try:
            await asyncio.sleep(self.delay)
            if self.fail:
                raise RuntimeError('SECRET-STACK-TRACE')
            answer = 'Ответ: ' + messages[-1]['content']
            emit({'type': 'delta', 'text': answer})
            return answer, {'prompt_eval_count': 42, 'eval_count': 6, 'done_reason': 'stop'}
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        finally:
            self.active -= 1


class FakeRetrieval:
    index = object()
    def load(self):
        pass
    def search(self, query):
        return [{'citation_id': 'S1', 'title': 'Документ', 'quote': 'Точная цитата из документа.',
                 'source': 'fixture.md', 'source_url': None, 'line_start': 1, 'line_end': 2}]


def config(tmp_path, **changes):
    values = dict(keys=(KEY, OTHER), db_path=tmp_path / 'chat.sqlite', log_path=tmp_path / 'log.jsonl',
                  tokenizer_path=tmp_path / 'absent.json', prompt_path=ROOT / 'prompts/system.txt',
                  rate_limit=100, generation_timeout=2, queue_timeout=2)
    return Config(**{**values, **changes})


@asynccontextmanager
async def client(cfg, runtime=None):
    runtime = runtime or FakeRuntime()
    app = create_app(cfg, runtime, FakeRetrieval())
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as c:
            yield c, runtime, app


def run(coro):
    return asyncio.run(coro)


def test_auth_history_isolation_and_persistence(tmp_path):
    async def scenario():
        cfg = config(tmp_path)
        async with client(cfg) as (c, model, app):
            assert (await c.get('/health')).status_code == 200
            for route in ('/api/status', '/api/sessions'):
                assert (await c.get(route)).status_code == 401
                assert (await c.get(route, headers={'Authorization': 'Bearer wrong'})).status_code == 401
            assert (await c.post('/api/chat', json={'message': 'test'})).status_code == 401
            first = await c.post('/api/chat', headers=AUTH, json={'message': 'ПЕРВЫЙ'})
            assert first.status_code == 200
            sid = first.json()['session_id']
            assert first.json()['answer'] == 'Ответ: ПЕРВЫЙ'
            url = f'/api/sessions/{sid}/messages'
            assert (await c.get(url)).status_code == 401
            rows = (await c.get(url, headers=AUTH)).json()['messages']
            assert [r['role'] for r in rows] == ['user', 'assistant']
            assert (await c.get(url, headers={'Authorization': 'Bearer ' + OTHER})).status_code == 404
            other = await c.post('/api/chat', headers={'Authorization': 'Bearer ' + OTHER}, json={'session_id': sid, 'message': 'hack'})
            assert other.status_code == 404
            empty = (await c.get('/api/sessions', headers={'Authorization': 'Bearer ' + OTHER})).json()
            assert empty['sessions'] == []
            second = (await c.post('/api/chat', headers=AUTH, json={'message': 'ВТОРОЙ'})).json()
            assert second['session_id'] != sid
            await c.post('/api/chat', headers=AUTH, json={'session_id': sid, 'message': 'ПРОДОЛЖЕНИЕ'})
            assert [m['content'] for m in model.calls[-1]][1:3] == ['ПЕРВЫЙ', 'Ответ: ПЕРВЫЙ']
            assert all('ВТОРОЙ' not in m['content'] for m in model.calls[-1])
        import hashlib
        reopened = Store(cfg.db_path)
        assert len(reopened.messages(sid, hashlib.sha256(KEY.encode()).hexdigest())) == 4
    run(scenario())


def test_validation_size_chunked_and_context(tmp_path):
    async def scenario():
        async with client(config(tmp_path)) as (c, model, app):
            for payload in ({'message': ''}, {'message': 123}, {'message': 'a', 'use_rag': 'yes'},
                            {'message': 'a', 'session_id': '../../file'}, {'message': 'a', 'unknown': 1}, {'message': '\ud800'}):
                assert (await c.post('/api/chat', headers={**AUTH, 'Content-Type': 'application/json'}, content=json.dumps(payload).encode())).status_code == 400
            assert (await c.post('/api/chat', headers=AUTH, content='{bad')).status_code == 400
            assert (await c.post('/api/chat', headers=AUTH, json={'message': 'x' * 70000})).status_code == 413
            async def chunks():
                for _ in range(8):
                    yield b'x' * 10000
            assert (await c.post('/api/chat', headers=AUTH, content=chunks())).status_code == 413
            assert (await c.post('/api/chat', headers=AUTH, json={'message': 'z' * 10000})).status_code == 422
            assert model.calls == []
    run(scenario())


def test_rate_limit_retry_after_and_independent_keys(tmp_path):
    async def scenario():
        async with client(config(tmp_path, rate_limit=1, rate_window=.1)) as (c, _, __):
            assert (await c.post('/api/chat', headers=AUTH, json={'message': 'a'})).status_code == 200
            limited = await c.post('/api/chat', headers=AUTH, json={'message': 'b'})
            assert limited.status_code == 429 and int(limited.headers['Retry-After']) >= 1
            assert (await c.post('/api/chat', headers={'Authorization': 'Bearer ' + OTHER}, json={'message': 'c'})).status_code == 200
            await asyncio.sleep(.11)
            assert (await c.post('/api/chat', headers=AUTH, json={'message': 'd'})).status_code == 200
    run(scenario())


def test_five_simultaneous_are_serial_and_matching(tmp_path):
    async def scenario():
        async with client(config(tmp_path)) as (c, runtime, app):
            runtime.delay = .035
            results = await asyncio.gather(*(c.post('/api/chat', headers=AUTH, json={'message': f'CODE-{i}'}) for i in range(5)))
            assert all(r.status_code == 200 for r in results)
            assert [r.json()['answer'] for r in results] == [f'Ответ: CODE-{i}' for i in range(5)]
            assert len({r.json()['request_id'] for r in results}) == 5
            assert runtime.max_active == 1
            assert sum(r.json()['metrics']['queue_ms'] > 10 for r in results) == 4
            assert app.state.queue.status()['waiting'] == 0
    run(scenario())


def test_queue_full_timeout_and_recovery(tmp_path):
    async def scenario():
        async with client(config(tmp_path, queue_size=1, queue_timeout=.03)) as (c, runtime, app):
            runtime.delay = .12
            first = asyncio.create_task(c.post('/api/chat', headers=AUTH, json={'message': 'first'}))
            await asyncio.sleep(.015)
            second = asyncio.create_task(c.post('/api/chat', headers=AUTH, json={'message': 'waiting'}))
            await asyncio.sleep(.01)
            rejected = await c.post('/api/chat', headers=AUTH, json={'message': 'overflow'})
            assert rejected.status_code == 503 and rejected.json()['error']['code'] == 'queue_full'
            expired = await second
            assert expired.status_code == 503 and expired.json()['error']['code'] == 'queue_timeout'
            assert (await first).status_code == 200
            runtime.delay = .001
            assert (await c.post('/api/chat', headers=AUTH, json={'message': 'after'})).status_code == 200
    run(scenario())


def test_generation_timeout_cancels_and_next_request_succeeds(tmp_path):
    async def scenario():
        async with client(config(tmp_path, generation_timeout=.025)) as (c, runtime, app):
            runtime.delay = .12
            failed = await c.post('/api/chat', headers=AUTH, json={'message': 'slow'})
            assert failed.status_code == 504
            assert runtime.cancelled == 1 and runtime.active == 0
            runtime.delay = .001
            assert (await c.post('/api/chat', headers=AUTH, json={'message': 'after'})).status_code == 200
    run(scenario())


def test_runtime_unavailable_and_recovery(tmp_path):
    async def scenario():
        async with client(config(tmp_path)) as (c, runtime, app):
            runtime.available = False
            assert (await c.get('/api/status', headers=AUTH)).json()['runtime']['available'] is False
            assert (await c.post('/api/chat', headers=AUTH, json={'message': 'a'})).status_code == 503
            runtime.available = True
            assert (await c.post('/api/chat', headers=AUTH, json={'message': 'b'})).status_code == 200
    run(scenario())


def test_stream_rag_sources_and_safe_metadata_logs(tmp_path):
    async def scenario():
        cfg = config(tmp_path)
        async with client(cfg) as (c, runtime, app):
            response = await c.post('/api/chat', headers=AUTH, json={'message': 'PRIVATE-MESSAGE', 'use_rag': True, 'stream': True})
            frames = [json.loads(line) for line in response.text.splitlines()]
            assert frames[0]['type'] == 'generating'
            assert any(f['type'] == 'delta' for f in frames)
            assert frames[-1]['type'] == 'done'
            assert frames[-1]['sources'][0]['quote'] == 'Точная цитата из документа.'
            assert response.headers['content-security-policy'].startswith("default-src 'self'")
            runtime.fail = True
            failed = await c.post('/api/chat', headers=AUTH, json={'message': 'PRIVATE-MESSAGE'})
            assert failed.status_code == 500 and 'SECRET-STACK' not in failed.text
        logs = cfg.log_path.read_text(encoding='utf-8')
        assert all(secret not in logs for secret in (KEY, OTHER, 'PRIVATE-MESSAGE', 'SECRET-STACK', 'Authorization'))
        rows = [json.loads(line) for line in logs.splitlines()]
        assert all({'request_id', 'processing_ms', 'http_status', 'queue_length', 'error_type'} <= set(r) for r in rows)
    run(scenario())


def test_stream_timeout_is_terminal_error(tmp_path):
    async def scenario():
        async with client(config(tmp_path, generation_timeout=.02)) as (c, runtime, app):
            runtime.delay = .1
            response = await c.post('/api/chat', headers=AUTH, json={'message': 'slow', 'stream': True})
            frames = [json.loads(line) for line in response.text.splitlines()]
            assert response.status_code == 200  # Headers already sent; error status travels in final frame.
            assert frames[-1]['type'] == 'error' and frames[-1]['status'] == 504
            assert app.state.queue.status()['active'] == 0
    run(scenario())


def test_context_preserves_rules_and_latest_message_trims_history_and_rag(tmp_path):
    cfg = config(tmp_path, context_window=3000, max_output_tokens=256, rag_max_tokens=2500)
    builder = ContextBuilder(cfg)
    history = [{'role': role, 'content': str(i) + 'a' * 1000} for i in range(6) for role in ('user', 'assistant')]
    sources = [{'citation_id': 'S1', 'title': 'Title', 'quote': 'word ' * 2000}]
    prepared = builder.build('Новое сообщение', history, sources)
    assert prepared.messages[0]['content'] == builder.system
    assert prepared.messages[-1]['content'] == 'Новое сообщение'
    assert prepared.budget['history_messages_removed'] > 0
    assert prepared.budget['input_tokens_budgeted'] + 256 <= 3000
    assert not prepared.sources or len(prepared.sources[0]['quote']) < len(sources[0]['quote'])


def test_cors_exact_origins_and_secret_free_errors(tmp_path):
    async def scenario():
        async with client(config(tmp_path, cors_origins=('http://allowed.local',))) as (c, runtime, app):
            good = await c.options('/api/chat', headers={'Origin': 'http://allowed.local', 'Access-Control-Request-Method': 'POST', 'Access-Control-Request-Headers': 'Authorization'})
            assert good.headers['Access-Control-Allow-Origin'] == 'http://allowed.local'
            bad = await c.options('/api/chat', headers={'Origin': 'http://evil.local', 'Access-Control-Request-Method': 'POST'})
            assert bad.status_code == 400 and 'Access-Control-Allow-Origin' not in bad.headers
    run(scenario())


@pytest.mark.parametrize('url', ['https://example.com', 'http://192.168.1.4:11434', 'http://127.0.0.1:11434/path', 'http://user:pass@localhost:11434'])
def test_nonlocal_runtime_config_rejected(tmp_path, url):
    with pytest.raises(ValueError):
        config(tmp_path, runtime_url=url).validate()


def test_cancelled_job_releases_capacity(tmp_path):
    async def scenario():
        stopped = asyncio.Event()
        async def runner(job):
            try:
                await asyncio.sleep(30)
            finally:
                stopped.set()
        q = GenerationQueue(config(tmp_path), runner)
        job = Job('rid', 'owner', 'sid', 'text', False)
        q.submit(job)
        await asyncio.sleep(.01)
        await q.cancel(job)
        assert stopped.is_set() and q.status()['active'] == 0 and 'sid' not in q.busy_sessions
        await q.close()
    run(scenario())


def test_runtime_remote_model_and_wrong_context_rejected(tmp_path):
    async def scenario():
        cfg = config(tmp_path)
        runtime = Runtime(cfg)
        await runtime.client.aclose()
        mode = ['remote']
        def respond(request):
            if request.url.path == '/api/tags':
                model = {'name': cfg.model}
                if mode[0] == 'remote': model['remote_host'] = 'cloud'
                return httpx.Response(200, json={'models': [model]})
            if request.url.path == '/api/ps':
                return httpx.Response(200, json={'models': [{'name': cfg.model, 'context_length': 4096}]})
            return httpx.Response(200, content=json.dumps({'message': {'content': 'answer'}, 'done': True}) + '\n')
        runtime.client = httpx.AsyncClient(base_url=cfg.runtime_url, transport=httpx.MockTransport(respond))
        with pytest.raises(ServiceError, match='model_unavailable'):
            await runtime.status()
        mode[0] = 'local'
        with pytest.raises(ServiceError, match='context_mismatch'):
            await runtime.generate([{'role': 'user', 'content': 'hi'}], lambda _: None)
        await runtime.close()
    run(scenario())


def test_env_example_loads_float_options_and_vpn_host(tmp_path, monkeypatch):
    import private_llm.config as module
    example = (ROOT / '.env.example').read_text(encoding='utf-8')
    example = example.replace('SERVICE_API_KEY=\n', 'SERVICE_API_KEY=' + KEY + '\n', 1)
    example = example.replace('SYSTEM_PROMPT_PATH=prompts/system.txt', 'SYSTEM_PROMPT_PATH=' + str(ROOT / 'prompts/system.txt'))
    (tmp_path / '.env').write_text(example, encoding='utf-8')
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    cfg = Config.load()
    assert cfg.temperature == 0.0 and cfg.generation_timeout == 120.0
    cfg.host = '100.100.100.20'
    cfg.validate()


def test_default_queue_capacity_and_default_rate_limit(tmp_path):
    async def scenario():
        async with client(config(tmp_path, rate_limit=10)) as (c, runtime, app):
            runtime.delay = .07
            tasks = [asyncio.create_task(c.post('/api/chat', headers=AUTH, json={'message': str(i)})) for i in range(6)]
            await asyncio.sleep(.025)
            assert app.state.queue.status() == {'active': 1, 'waiting': 5, 'concurrency': 1, 'max_waiting': 5}
            assert (await c.post('/api/chat', headers=AUTH, json={'message': 'seventh'})).status_code == 503
            assert all(r.status_code == 200 for r in await asyncio.gather(*tasks))
            runtime.delay = .001
            for i in range(3):
                assert (await c.post('/api/chat', headers=AUTH, json={'message': str(i)})).status_code == 200
            limited = await c.post('/api/chat', headers=AUTH, json={'message': 'eleventh'})
            assert limited.status_code == 429 and 'Retry-After' in limited.headers
    run(scenario())


def test_immediate_cancellation_releases_reservation(tmp_path):
    async def scenario():
        async def runner(job):
            await asyncio.sleep(10)
        q = GenerationQueue(config(tmp_path), runner)
        job = Job('rid', 'owner', 'sid', 'text', False)
        q.submit(job)
        await q.cancel(job)
        assert q.status()['active'] == 0 and job.result.done() and not q.busy_sessions
        await q.close()
    run(scenario())
