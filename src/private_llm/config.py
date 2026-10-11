from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit
import ipaddress
import os
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
BASE = Path(r'D:\AIAdvent\advent-day-29-optimized-local-rag')


@dataclass
class Config:
    keys: tuple[str, ...] = field(default_factory=tuple, repr=False)
    host: str = '127.0.0.1'
    port: int = 8030
    runtime_url: str = 'http://127.0.0.1:11434'
    model: str = 'qwen3.5:4b'
    context_window: int = 8192
    max_output_tokens: int = 512
    rate_limit: int = 10
    rate_window: float = 60.0
    concurrency: int = 1
    queue_size: int = 5
    queue_timeout: float = 600.0
    generation_timeout: float = 120.0
    max_body_bytes: int = 65536
    rag_enabled: bool = True
    rag_top_k: int = 3
    rag_max_tokens: int = 2000
    history_limit: int = 100
    tokenizer_path: Path = BASE / 'models/tokenizer.json'
    index_path: Path = BASE / 'output/structural.sqlite'
    db_path: Path = ROOT / 'data/sessions.sqlite'
    log_path: Path = ROOT / 'logs/service.jsonl'
    prompt_path: Path = ROOT / 'prompts/system.txt'
    cors_origins: tuple[str, ...] = ()
    temperature: float = 0.0
    top_p: float = .95
    top_k: int = 20
    presence_penalty: float = 1.5
    seed: int = 28
    num_gpu: int = 0
    num_thread: int = 4

    @classmethod
    def load(cls):
        env = {**dotenv_values(ROOT / '.env', interpolate=False), **os.environ}
        config = cls()
        mapping = {
            'HOST': 'host', 'PORT': 'port', 'LOCAL_LLM_BASE_URL': 'runtime_url',
            'LOCAL_LLM_MODEL': 'model', 'CONTEXT_WINDOW': 'context_window',
            'MAX_OUTPUT_TOKENS': 'max_output_tokens', 'RATE_LIMIT': 'rate_limit',
            'RATE_WINDOW_SECONDS': 'rate_window', 'GENERATION_CONCURRENCY': 'concurrency',
            'QUEUE_SIZE': 'queue_size', 'QUEUE_TIMEOUT_SECONDS': 'queue_timeout',
            'GENERATION_TIMEOUT_SECONDS': 'generation_timeout', 'MAX_BODY_BYTES': 'max_body_bytes',
            'RAG_ENABLED': 'rag_enabled', 'RAG_TOP_K': 'rag_top_k', 'RAG_MAX_TOKENS': 'rag_max_tokens',
            'HISTORY_LIMIT': 'history_limit', 'LOCAL_TOKENIZER_PATH': 'tokenizer_path',
            'LOCAL_INDEX_PATH': 'index_path', 'DATABASE_PATH': 'db_path', 'LOG_PATH': 'log_path',
            'SYSTEM_PROMPT_PATH': 'prompt_path', 'TEMPERATURE': 'temperature', 'TOP_P': 'top_p',
            'TOP_K': 'top_k', 'PRESENCE_PENALTY': 'presence_penalty', 'SEED': 'seed',
            'NUM_GPU': 'num_gpu', 'NUM_THREAD': 'num_thread'}
        for name, attr in mapping.items():
            value = env.get(name)
            if value is None or value == '':
                continue
            old = getattr(config, attr)
            if isinstance(old, bool):
                if value.lower() not in ('true', 'false', '1', '0'):
                    raise ValueError(f'{name}: ожидается true/false')
                value = value.lower() in ('true', '1')
            elif isinstance(old, Path):
                value = Path(value)
                if not value.is_absolute():
                    value = ROOT / value
            else:
                try:
                    value = type(old)(value)
                except ValueError:
                    raise ValueError(f'{name}: неверный формат') from None
            setattr(config, attr, value)
        config.keys = tuple(k.strip() for k in
                            (env.get('SERVICE_API_KEY', '') + ',' + env.get('ADDITIONAL_API_KEYS', '')).split(',')
                            if k.strip())
        config.cors_origins = tuple(o.strip().rstrip('/') for o in env.get('CORS_ORIGINS', '').split(',') if o.strip())
        config.validate()
        return config

    def validate(self):
        if not self.keys or any(len(k) < 32 or not k.isascii() or any(c.isspace() for c in k) for k in self.keys):
            raise ValueError('Задайте SERVICE_API_KEY: не менее 32 ASCII-символов без пробелов')
        url = urlsplit(self.runtime_url)
        if (url.scheme != 'http' or url.hostname not in ('127.0.0.1', '::1', 'localhost')
                or url.username or url.password or url.query or url.fragment or url.path not in ('', '/')):
            raise ValueError('LOCAL_LLM_BASE_URL должен указывать только на HTTP loopback Ollama')
        if not self.model or 'cloud' in self.model.lower():
            raise ValueError('Нужна установленная локальная модель')
        try:
            host = ipaddress.ip_address(self.host)
        except ValueError:
            if self.host != 'localhost':
                raise ValueError('HOST: задайте localhost или локальный IP-адрес') from None
        else:
            if not (host.is_private or host.is_loopback or host.is_unspecified or host in ipaddress.ip_network('100.64.0.0/10')):
                raise ValueError('Публичный HOST запрещён; используйте LAN/VPN')
        positive = ('port', 'context_window', 'max_output_tokens', 'rate_limit', 'rate_window',
                    'concurrency', 'queue_size', 'queue_timeout', 'generation_timeout',
                    'max_body_bytes', 'rag_top_k', 'rag_max_tokens', 'history_limit', 'num_thread')
        if any(getattr(self, n) <= 0 for n in positive) or self.port > 65535:
            raise ValueError('Лимиты должны быть положительными; PORT <= 65535')
        if self.max_output_tokens + 512 >= self.context_window:
            raise ValueError('CONTEXT_WINDOW слишком мал для правил и резерва ответа')
        if not self.prompt_path.is_file():
            raise ValueError('Не найден SYSTEM_PROMPT_PATH')
        for origin in self.cors_origins:
            parsed = urlsplit(origin)
            if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.path or parsed.query or parsed.fragment or parsed.username or '*' in origin:
                raise ValueError('CORS_ORIGINS: только точные origin без wildcard и пути')
        if not 0 <= self.temperature <= 2 or not 0 < self.top_p <= 1 or not 0 <= self.presence_penalty <= 2:
            raise ValueError('Проверьте параметры sampling')

    def options(self):
        return dict(num_ctx=self.context_window, num_predict=self.max_output_tokens,
                    temperature=self.temperature, top_p=self.top_p, top_k=self.top_k,
                    presence_penalty=self.presence_penalty, seed=self.seed,
                    num_gpu=self.num_gpu, num_thread=self.num_thread)
