"""Read-only Day 29 E5 retrieval; no network downloads or cloud rewrites."""
from .errors import ServiceError


class Retrieval:
    def __init__(self, config):
        self.config, self.index, self.embedding = config, None, None

    def load(self):
        if not self.config.rag_enabled:
            return
        from document_indexer.storage import load_index
        from document_indexer.embeddings import get_embedding_service
        index = load_index(self.config.index_path)
        embedding = get_embedding_service(index.config['model_id'], index.config['revision'])
        self.index, self.embedding = index, embedding

    def search(self, message):
        if self.index is None:
            raise ServiceError(503, 'rag_unavailable', 'Локальный индекс или embedding-модель недоступны.')
        from document_indexer.search import search
        # E5 has its own 512-token limit. Trim only the search query, never the user message.
        tokenizer = self.embedding.model.tokenizer
        ids = tokenizer.encode(message, add_special_tokens=False)
        query = tokenizer.decode(ids[:self.embedding.limit - 16], skip_special_tokens=True)
        results = search(self.index, query, self.config.rag_top_k, service=self.embedding)['results']
        return [{'citation_id': f'S{i}', 'chunk_id': r['chunk_id'], 'title': r['title'],
                 'source': r['source'], 'source_url': r['source_url'], 'score': r['score'],
                 'line_start': r['line_start'], 'line_end': r['line_end'],
                 'page_start': r['page_start'], 'page_end': r['page_end'],
                 'quote': r['text'], 'truncated': False} for i, r in enumerate(results, 1)]
