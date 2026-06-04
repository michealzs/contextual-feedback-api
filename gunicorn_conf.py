"""Gunicorn config.

The async analysis runs in a background thread inside the worker and writes to
SQLite; keep workers modest. For heavy concurrency, move to a shared DB and a
real task queue.
"""
from __future__ import annotations

import os

bind = f"0.0.0.0:{os.environ.get('CONTEXTUAL_PORT', '5000')}"
workers = int(os.environ.get("WEB_CONCURRENCY", "2"))
threads = int(os.environ.get("GUNICORN_THREADS", "4"))
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "120"))
graceful_timeout = 30
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("GUNICORN_LOG_LEVEL", "info")
