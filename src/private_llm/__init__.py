"""Day 30: a local, authenticated Ollama service."""
import os

os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                  HF_HUB_DISABLE_TELEMETRY='1', RAG_ALLOW_DOWNLOAD='0')
