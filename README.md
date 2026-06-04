# contextual-feedback

A Flask document-analysis API that simulates model inference over large files.
Upload PDF/TXT documents, define **context configurations** (token windows,
overlap, temperature), then kick off **asynchronous** chunk-based analysis with
optional **webhook** delivery — all behind API-key auth, with structured request
logging, request-ID propagation, and a full operational layer (Prometheus
metrics, Docker, monitoring, CI/CD).

## Install

```bash
pip install -e ".[dev]"
```

## Run

```bash
# dev
contextual-feedback          # or: python -m contextual_feedback
# production
gunicorn contextual_feedback.wsgi:app -c gunicorn_conf.py
```

Listens on `0.0.0.0:5000`. State lives under `CONTEXTUAL_DATA_DIR` (default
`/app`): the SQLite DB (`data/documents.db`), uploads, and logs.

## Auth

Every endpoint except `GET /api/v1/health` and `GET /metrics` requires an
`X-API-Key` header. The expected key comes from `API_KEY` (default
`terminus-dev-key`). Missing/wrong key ⇒ 401.

## Endpoints

| Method & path | Purpose |
| --- | --- |
| `GET /api/v1/health` | `{"status":"healthy","count":N}` (no auth) |
| `GET /metrics` | Prometheus metrics (no auth) |
| `POST /api/v1/documents` | Upload PDF/TXT (multipart `file`, ≤10 MB) |
| `GET /api/v1/documents` | List (bare array; `page`/`per_page`) |
| `GET/DELETE /api/v1/documents/<id>` | Fetch / delete a document |
| `POST/GET/PUT /api/v1/context[/<id>]` | Context configs (validated) |
| `POST /api/v1/analysis` | Start async analysis → `202` + job |
| `GET /api/v1/jobs[/<id>]` | Job state(s) |
| `GET /api/v1/results[/<id>]` | Analysis result(s) |

```bash
KEY=terminus-dev-key
curl -H "X-API-Key: $KEY" -F "file=@doc.txt" localhost:5000/api/v1/documents
curl -H "X-API-Key: $KEY" -H 'content-type: application/json' \
  -d '{"max_tokens":1000,"overlap_tokens":100,"temperature":0.5}' \
  localhost:5000/api/v1/context
curl -H "X-API-Key: $KEY" -H 'content-type: application/json' \
  -d '{"document_id":1,"context_id":1}' localhost:5000/api/v1/analysis
```

## Configuration

| Env var | Default | Meaning |
| --- | --- | --- |
| `CONTEXTUAL_DATA_DIR` | `/app` | Root for `data/`, `uploads/`, `logs/` |
| `API_KEY` | `terminus-dev-key` | Required `X-API-Key` value |
| `CONTEXTUAL_PORT` | `5000` | Listen port |
| `WEB_CONCURRENCY` | `2` | Gunicorn workers (Docker) |

## Tests

```bash
pytest
```

Boots the app on a temporary data dir and exercises auth, uploads, validation,
context configs, the async analysis flow (job → result), webhooks, pagination,
request-ID echo, and the metrics endpoint.

## Docker & monitoring

```bash
docker build -t contextual-feedback .
docker run -p 5000:5000 -v cfb_data:/app contextual-feedback

# full stack with Prometheus + Grafana
docker compose --profile monitoring up -d --build
```

- Metrics: http://localhost:5000/metrics
- Prometheus: http://localhost:9090
- Grafana: http://localhost:3000 (admin/admin) — auto-provisioned dashboard

## License

MIT.
