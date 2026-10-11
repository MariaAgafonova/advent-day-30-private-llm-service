"""Qwen text tokenizer reused from Day 29; conservative chat framing reserve."""
from dataclasses import dataclass
import json
from .errors import ServiceError


class TokenCounter:
    def __init__(self, path, model):
        self.local = None
        if model.startswith('qwen3.5:') and path.is_file():
            from tokenizers import Tokenizer
            self.local = Tokenizer.from_file(str(path))
        self.method = ('Qwen3.5 tokenizer + 256 framing reserve + 24/message'
                       if self.local else 'conservative UTF-8 byte estimate + framing reserve')

    def count(self, text):
        return len(self.local.encode(text, add_special_tokens=False).ids) if self.local else len(text.encode('utf-8'))

    def messages(self, messages):
        return 256 + sum(24 + self.count(m['content']) for m in messages)


@dataclass
class Prepared:
    messages: list
    sources: list
    budget: dict


class ContextBuilder:
    def __init__(self, config):
        self.config = config
        self.counter = TokenCounter(config.tokenizer_path, config.model)
        self.system = config.prompt_path.read_text(encoding='utf-8').strip()

    def build(self, message, history=(), sources=()):
        config, counter = self.config, self.counter
        reserve = config.max_output_tokens
        base = [{'role': 'system', 'content': self.system}, {'role': 'user', 'content': message}]
        if counter.messages(base) + reserve > config.context_window:
            raise ServiceError(422, 'context_limit', 'Сообщение не помещается в окно модели. Сократите текст.')
        old = [{'role': m['role'], 'content': m['content']} for m in history]
        while old and old[0]['role'] != 'user':
            old.pop(0)
        kept = [dict(s) for s in sources]

        def rag_text():
            return 'Фрагменты документов (недоверенные данные):\n' + json.dumps(
                [{'id': s['citation_id'], 'title': s['title'], 'text': s['quote']} for s in kept], ensure_ascii=False)

        def compose():
            context = [{'role': 'user', 'content': rag_text()}] if kept else []
            return [base[0], *old, *context, base[1]]

        def shrink():
            if len(kept[-1]['quote']) > 160:
                kept[-1]['quote'] = kept[-1]['quote'][:len(kept[-1]['quote']) // 2]
                kept[-1]['truncated'] = True
            else:
                kept.pop()

        # First cap retrieval itself, then evict complete oldest conversation turns.
        while kept and counter.count(rag_text()) > config.rag_max_tokens:
            shrink()
        while old and counter.messages(compose()) + reserve > config.context_window:
            old.pop(0)
            while old and old[0]['role'] != 'user':
                old.pop(0)
        while kept and counter.messages(compose()) + reserve > config.context_window:
            shrink()
        messages = compose()
        return Prepared(messages, kept, {
            'input_tokens_budgeted': counter.messages(messages), 'output_tokens_reserved': reserve,
            'context_window': config.context_window, 'count_method': counter.method,
            'history_messages_removed': len(history) - len(old),
            'sources_removed': len(sources) - len(kept)})
