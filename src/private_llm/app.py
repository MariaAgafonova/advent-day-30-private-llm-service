import asyncio
from contextlib import asynccontextmanager
import hashlib
import hmac
import json
import logging
from logging.handlers import RotatingFileHandler
from time import monotonic
from uuid import UUID

from fastapi import FastAPI, Request, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, StrictBool, StrictStr, field_validator
from filelock import FileLock, Timeout

from .config import Config, ROOT
from .context import ContextBuilder
from .errors import ServiceError
from .middleware import Guard
from .queue import GenerationQueue, RateLimiter, Job
from .retrieval import Retrieval
from .runtime import Runtime
from .storage import Store


class ChatInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    session_id: UUID | None = None
    message: StrictStr
    use_rag: StrictBool = False
    stream: StrictBool = False

    @field_validator('message')
    @classmethod
    def message_nonempty(cls, value):
        if not value.strip():
            raise ValueError('empty')
        try:
            value.encode('utf-8')
        except UnicodeEncodeError:
            raise ValueError('invalid unicode') from None
        return value.strip()


def create_app(config=None, runtime=None, retrieval=None):
    config = config or Config.load()
    config.validate()
    store, builder = Store(config.db_path), ContextBuilder(config)
    runtime, retrieval = runtime or Runtime(config), retrieval or Retrieval(config)
    limiter = RateLimiter(config.rate_limit, config.rate_window)
    logger = logging.getLogger('private_llm.' + hashlib.sha256(str(config.log_path).encode()).hexdigest()[:12])
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        config.log_path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(config.log_path, maxBytes=2_000_000, backupCount=3, encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(message)s'))
        logger.addHandler(handler)

    async def run(job):
        history = store.messages(job.session_id, job.owner, config.history_limit)
        sources = await asyncio.to_thread(retrieval.search, job.message) if job.use_rag else []
        prepared = builder.build(job.message, history, sources)
        first_token = None

        def emit(event):
            nonlocal first_token
            if event['type'] == 'delta' and first_token is None:
                first_token = (monotonic() - job.started) * 1000
            job.emit(event)

        answer, metrics = await runtime.generate(prepared.messages, emit)
        metrics.update(queue_ms=round((job.started-job.created)*1000, 2),
                       generation_ms=round((monotonic()-job.started)*1000, 2),
                       total_ms=round((monotonic()-job.created)*1000, 2),
                       first_token_ms=round(first_token, 2) if first_token is not None else None,
                       **prepared.budget)
        store.save_turn(job.session_id, job.owner, job.request_id, job.message, answer, prepared.sources, metrics)
        return dict(request_id=job.request_id, session_id=job.session_id, answer=answer,
                    sources=prepared.sources, metrics=metrics)

    queue = GenerationQueue(config, run)

    @asynccontextmanager
    async def lifespan(app):
        lock = FileLock(str(config.db_path) + '.service.lock')
        try:
            lock.acquire(timeout=0)
        except Timeout:
            raise RuntimeError('Эта база уже используется сервисом. Запускайте один процесс/worker.') from None
        try:
            try:
                await asyncio.to_thread(retrieval.load)
            except Exception:
                app.state.rag_error = 'rag_unavailable'
            yield
        finally:
            await queue.close()
            await runtime.close()
            lock.release()

    app = FastAPI(title='Приватная LLM', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.queue, app.state.config = queue, config
    app.state.store, app.state.runtime = store, runtime
    app.add_middleware(Guard, config=config, logger=logger, queue_status=queue.status)
    app.add_middleware(CORSMiddleware, allow_origins=list(config.cors_origins),
                       allow_methods=['GET', 'POST'], allow_headers=['Authorization', 'Content-Type'],
                       expose_headers=['Retry-After', 'X-Request-ID'])

    def authorize(request):
        raw = request.headers.get('authorization', '')
        supplied = raw[7:] if raw.startswith('Bearer ') else ''
        matched = False
        for key in config.keys:
            matched = hmac.compare_digest(supplied.encode(), key.encode()) or matched
        if not matched:
            raise ServiceError(401, 'unauthorized', 'Введите действующий API-ключ.')
        # No key or authorization header is persisted; one key denotes one user.
        return hashlib.sha256(supplied.encode()).hexdigest()

    @app.exception_handler(ServiceError)
    async def service_error(request, exc):
        request.state.error_type = exc.code
        headers = {'Retry-After': str(exc.retry_after)} if exc.retry_after is not None else {}
        if exc.status == 401:
            headers['WWW-Authenticate'] = 'Bearer'
        return JSONResponse(exc.body(request.state.request_id), status_code=exc.status, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        return await service_error(request, ServiceError(400, 'invalid_request', 'Проверьте формат JSON, session_id и непустое текстовое message.'))

    @app.exception_handler(Exception)
    async def unexpected(request, exc):
        return await service_error(request, ServiceError(500, 'internal_error', 'Внутренняя ошибка сервиса.'))

    @app.get('/health')
    async def health():
        return {'status': 'ok', 'service': 'private-llm', 'version': '1.0.0'}

    @app.get('/api/status')
    async def status(request: Request):
        authorize(request)
        try:
            model = await runtime.status()
        except ServiceError as exc:
            model = {'available': False, 'model': config.model, 'error': exc.code}
        return {'runtime': model, 'queue': queue.status(),
                'rag': {'enabled': config.rag_enabled, 'available': retrieval.index is not None},
                'limits': {'rate_limit': config.rate_limit, 'rate_window_seconds': config.rate_window,
                           'max_body_bytes': config.max_body_bytes, 'generation_timeout_seconds': config.generation_timeout,
                           'queue_timeout_seconds': config.queue_timeout, 'max_output_tokens': config.max_output_tokens,
                           'context_window': config.context_window, 'token_count_method': builder.counter.method}}

    @app.get('/api/sessions')
    async def sessions(request: Request):
        return {'sessions': store.sessions(authorize(request))}

    @app.get('/api/sessions/{session_id}/messages')
    async def messages(session_id: UUID, request: Request, limit: int = Query(100, ge=1, le=200),
                       before: int | None = Query(None, ge=1)):
        owner = authorize(request)
        rows = store.messages(str(session_id), owner, limit, before)
        return {'session_id': str(session_id), 'messages': rows,
                'next_before': rows[0]['id'] if len(rows) == limit else None}

    @app.post('/api/chat')
    async def chat(payload: ChatInput, request: Request):
        owner = authorize(request)
        sid = str(payload.session_id) if payload.session_id else None
        if sid:
            store.require(sid, owner)
        builder.build(payload.message)  # Reject irreducible overflows before queueing or LLM calls.
        if payload.use_rag and (not config.rag_enabled or retrieval.index is None):
            raise ServiceError(503, 'rag_unavailable', 'RAG недоступен. Выключите переключатель или проверьте локальный индекс.')
        limiter.check(owner)
        queue.check_capacity(sid)
        await runtime.status()
        # Recheck after await so concurrent requests cannot overbook capacity.
        queue.check_capacity(sid)
        sid = sid or store.create(owner, payload.message)
        job = Job(request.state.request_id, owner, sid, payload.message, payload.use_rag, payload.stream)
        queue.submit(job)

        def note_error(result):
            if isinstance(result, ServiceError):
                request.state.error_type = result.code
                request.state.result_status = result.status

        if not payload.stream:
            try:
                while not job.result.done():
                    await asyncio.wait([job.result], timeout=.2)
                    if await request.is_disconnected():
                        await queue.cancel(job)
                        raise ServiceError(499, 'cancelled', 'Клиент отключился.')
                result = job.result.result()
                note_error(result)
                if isinstance(result, ServiceError):
                    raise result
                return result
            finally:
                if not job.result.done():
                    await queue.cancel(job)

        async def stream():
            try:
                while not job.result.done() or not job.events.empty():
                    try:
                        event = await asyncio.wait_for(job.events.get(), timeout=.5)
                        yield json.dumps(event, ensure_ascii=False) + '\n'
                    except asyncio.TimeoutError:
                        if not job.result.done():
                            yield '{"type":"heartbeat"}\n'
                result = job.result.result()
                note_error(result)
                if isinstance(result, ServiceError):
                    event = {'type': 'error', 'status': result.status, 'session_id': sid, **result.body(job.request_id)}
                else:
                    event = {'type': 'done', **result}
                yield json.dumps(event, ensure_ascii=False) + '\n'
            finally:
                if not job.result.done():
                    await queue.cancel(job)

        return StreamingResponse(stream(), media_type='application/x-ndjson', headers={'X-Accel-Buffering': 'no'})

    @app.get('/')
    async def index():
        return FileResponse(ROOT / 'web/index.html')

    @app.get('/app.js')
    async def javascript():
        return FileResponse(ROOT / 'web/app.js', media_type='text/javascript')

    @app.get('/style.css')
    async def stylesheet():
        return FileResponse(ROOT / 'web/style.css', media_type='text/css')

    return app
