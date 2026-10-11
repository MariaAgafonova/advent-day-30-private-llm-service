"""Bounded FIFO, absolute execution/queue deadlines, cancellation and per-key limits."""
import asyncio
from collections import deque
from dataclasses import dataclass, field
import math
from time import monotonic
from .errors import ServiceError


class RateLimiter:
    def __init__(self, limit, window):
        self.limit, self.window, self.entries = limit, window, {}

    def check(self, owner):
        now = monotonic()
        bucket = self.entries.setdefault(owner, deque())
        while bucket and bucket[0] <= now - self.window:
            bucket.popleft()
        if len(bucket) >= self.limit:
            raise ServiceError(429, 'rate_limit', 'Превышен лимит запросов. Подождите перед повтором.',
                               max(1, math.ceil(bucket[0] + self.window - now)))
        bucket.append(now)


@dataclass(eq=False)
class Job:
    request_id: str
    owner: str
    session_id: str
    message: str
    use_rag: bool
    streaming: bool = False
    created: float = field(default_factory=monotonic)
    started: float | None = None
    events: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=128))
    result: asyncio.Future = field(default_factory=lambda: asyncio.get_running_loop().create_future())
    timer: asyncio.Task | None = None
    task: asyncio.Task | None = None

    def emit(self, event):
        if not self.streaming:
            return
        try:
            self.events.put_nowait(event)
        except asyncio.QueueFull:
            raise ServiceError(408, 'slow_client', 'Клиент слишком медленно принимает ответ.') from None


class GenerationQueue:
    def __init__(self, config, runner):
        self.config, self.runner = config, runner
        self.pending, self.active = deque(), set()
        self.busy_sessions, self.closed = set(), False

    def status(self):
        return dict(active=len(self.active), waiting=len(self.pending),
                    concurrency=self.config.concurrency, max_waiting=self.config.queue_size)

    def check_capacity(self, sid=None):
        if self.closed:
            raise ServiceError(503, 'shutting_down', 'Сервис останавливается.')
        if sid and sid in self.busy_sessions:
            raise ServiceError(409, 'session_busy', 'В этой сессии уже выполняется запрос.')
        if len(self.active) >= self.config.concurrency and len(self.pending) >= self.config.queue_size:
            raise ServiceError(503, 'queue_full', 'Очередь заполнена. Повторите запрос позже.', 5)

    def submit(self, job):
        self.check_capacity(job.session_id)
        self.busy_sessions.add(job.session_id)
        if len(self.active) < self.config.concurrency:
            self.start(job)
        else:
            self.pending.append(job)
            job.emit({'type': 'queued', 'position': len(self.pending), 'session_id': job.session_id,
                      'request_id': job.request_id})
            job.timer = asyncio.create_task(self.expire(job))

    def start(self, job):
        if job.timer:
            job.timer.cancel()
        self.active.add(job)
        job.started = monotonic()
        job.task = asyncio.create_task(self.execute(job))

    async def expire(self, job):
        await asyncio.sleep(self.config.queue_timeout)
        if job in self.pending:
            self.pending.remove(job)
            self.finish(job, ServiceError(503, 'queue_timeout', 'Время ожидания в очереди истекло.', 5))

    def finish(self, job, result):
        self.busy_sessions.discard(job.session_id)
        if not job.result.done():
            job.result.set_result(result)

    async def execute(self, job):
        try:
            job.emit({'type': 'generating', 'session_id': job.session_id, 'request_id': job.request_id})
            result = await asyncio.wait_for(self.runner(job), self.config.generation_timeout)
        except asyncio.TimeoutError:
            result = ServiceError(504, 'generation_timeout', 'Превышено время генерации. Сервис готов к следующему запросу.')
        except asyncio.CancelledError:
            result = ServiceError(499, 'cancelled', 'Запрос отменён.')
        except ServiceError as exc:
            result = exc
        except Exception:
            result = ServiceError(500, 'internal_error', 'Не удалось обработать запрос.')
        finally:
            self.active.discard(job)
        self.finish(job, result)
        if self.pending and not self.closed:
            self.start(self.pending.popleft())

    async def cancel(self, job):
        if job in self.pending:
            self.pending.remove(job)
            job.timer.cancel()
            self.finish(job, ServiceError(499, 'cancelled', 'Запрос отменён.'))
        if job in self.active:
            job.task.cancel()
            await asyncio.gather(job.task, return_exceptions=True)
            # Cancellation can arrive before the coroutine enters its try/finally.
            if job in self.active:
                self.active.discard(job)
                self.finish(job, ServiceError(499, 'cancelled', 'Запрос отменён.'))
                if self.pending and not self.closed:
                    self.start(self.pending.popleft())

    async def close(self):
        self.closed = True
        for job in list(self.pending) + list(self.active):
            await self.cancel(job)
