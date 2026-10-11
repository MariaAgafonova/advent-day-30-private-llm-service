"""Async adaptation of Day 29's loopback-only native Ollama streaming client."""
import json
import httpx
from .errors import ServiceError


class Runtime:
    def __init__(self, config):
        self.config = config
        self.client = httpx.AsyncClient(base_url=config.runtime_url.rstrip('/'),
                                        trust_env=False, follow_redirects=False, timeout=5)

    async def close(self):
        await self.client.aclose()

    async def status(self):
        try:
            tags = await self.client.get('/api/tags')
            tags.raise_for_status()
            model = next((m for m in tags.json()['models'] if m['name'] == self.config.model), None)
            if not model or model.get('remote_host') or model.get('remote_model'):
                raise ServiceError(503, 'model_unavailable', 'Выбранная локальная модель отсутствует в Ollama.')
            ps = await self.client.get('/api/ps')
            ps.raise_for_status()
            loaded = next((m for m in ps.json()['models'] if m['name'] == self.config.model), {})
            return dict(available=True, model=self.config.model, digest=model.get('digest'),
                        quantization=model.get('details', {}).get('quantization_level'),
                        loaded=bool(loaded), context_length=loaded.get('context_length'),
                        configured_context=self.config.context_window)
        except ServiceError:
            raise
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise ServiceError(503, 'runtime_unavailable', 'Ollama недоступен. Проверьте локальный runtime.') from None

    async def generate(self, messages, emit):
        await self.status()  # Check installed model; never invoke remote-backed/cloud models.
        body = dict(model=self.config.model, messages=messages, stream=True, think=False,
                    keep_alive='10m', options=self.config.options())
        answer, final = '', None
        try:
            async with self.client.stream('POST', '/api/chat', json=body,
                                          timeout=self.config.generation_timeout) as response:
                if response.status_code != 200:
                    raise ServiceError(503, 'runtime_error', 'Локальная модель отклонила запрос.')
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    frame = json.loads(line)
                    if frame.get('error'):
                        raise ServiceError(503, 'runtime_error', 'Ошибка локальной модели. Повторите запрос.')
                    delta = frame.get('message', {}).get('content', '')
                    if not isinstance(delta, str):
                        raise ValueError()
                    if delta:
                        answer += delta
                        if len(answer.encode('utf-8')) > 1024 * 1024:
                            raise ValueError()
                        emit({'type': 'delta', 'text': delta})
                    if frame.get('done'):
                        final = frame
                        break
            if not final or not answer.strip():
                raise ValueError()
            loaded = await self.status()
            if loaded['context_length'] != self.config.context_window:
                raise ServiceError(503, 'context_mismatch', 'Runtime не подтвердил настроенное окно контекста.')
            metrics = {k: final[k] for k in ('total_duration', 'load_duration', 'prompt_eval_duration',
                       'eval_duration', 'prompt_eval_count', 'eval_count', 'prompt_eval_cached_count')
                       if isinstance(final.get(k), (int, float))}
            if metrics.get('prompt_eval_count', 0) + self.config.max_output_tokens > self.config.context_window:
                raise ServiceError(503, 'runtime_context_overflow', 'Фактический контекст превысил бюджет.')
            metrics.update(done_reason=final.get('done_reason'), runtime_context_length=loaded['context_length'])
            return answer, metrics
        except httpx.TimeoutException:
            raise ServiceError(504, 'generation_timeout', 'Модель не ответила вовремя. Повторите или сократите запрос.') from None
        except httpx.HTTPError:
            raise ServiceError(503, 'runtime_unavailable', 'Соединение с Ollama прервано.') from None
        except (ValueError, KeyError, TypeError):
            raise ServiceError(502, 'invalid_runtime_response', 'Runtime вернул неполный или неверный ответ.') from None
