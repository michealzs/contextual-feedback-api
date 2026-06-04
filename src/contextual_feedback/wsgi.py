"""WSGI entrypoint for gunicorn: ``gunicorn contextual_feedback.wsgi:app``."""
from .app import app

__all__ = ["app"]
