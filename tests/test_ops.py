"""Operational-layer tests: request-ID echo, structured logging, metrics."""
from __future__ import annotations

import json

import requests
from conftest import upload_txt


def test_request_id_echoed(base, auth):
    r = requests.get(f"{base}/api/v1/health", headers={**auth, "X-Request-ID": "trace-xyz"}, timeout=10)
    assert r.headers.get("X-Request-ID") == "trace-xyz"


def test_structured_request_log(base, auth, data_dir):
    requests.get(f"{base}/api/v1/health", timeout=10)
    upload_txt(base, auth)
    log_path = data_dir / "logs" / "api.log"
    assert log_path.exists()
    lines = [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]
    assert lines
    last = lines[-1]
    assert {"timestamp", "method", "path", "status", "client_ip", "request_id"} <= set(last)


def test_metrics_endpoint_no_auth(base):
    requests.get(f"{base}/api/v1/health", timeout=10)
    r = requests.get(f"{base}/metrics", timeout=10)
    assert r.status_code == 200
    assert "flask_http_request" in r.text
