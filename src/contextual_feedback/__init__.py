"""Contextual feedback API — a Flask document-analysis service.

Upload documents, define context configurations, and run asynchronous,
chunk-based mock analysis with webhooks, all behind API-key auth.
"""
from .app import app, main

__version__ = "0.1.0"
__all__ = ["app", "main"]
