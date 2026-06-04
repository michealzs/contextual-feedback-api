# syntax=docker/dockerfile:1

# --- build stage -------------------------------------------------------------
FROM python:3.11-slim AS build

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install .

# --- runtime stage -----------------------------------------------------------
FROM python:3.11-slim AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    CONTEXTUAL_DATA_DIR=/app \
    WEB_CONCURRENCY=2

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/data /app/uploads /app/logs && chown -R appuser /app

WORKDIR /app
COPY --from=build /opt/venv /opt/venv
COPY gunicorn_conf.py docker-entrypoint.sh ./
RUN chmod +x docker-entrypoint.sh

VOLUME ["/app/data", "/app/uploads", "/app/logs"]
USER appuser
EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5000/api/v1/health').status==200 else 1)"

ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["gunicorn", "contextual_feedback.wsgi:app", "-c", "gunicorn_conf.py"]
