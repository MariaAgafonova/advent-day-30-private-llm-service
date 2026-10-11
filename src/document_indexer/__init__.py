"""Shared services for CLI and Streamlit."""
import os

if os.getenv('RAG_ALLOW_DOWNLOAD') != '1':
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
