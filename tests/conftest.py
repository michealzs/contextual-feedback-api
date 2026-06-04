"""Fixtures: boot the app as a subprocess against a temp data dir, plus a tiny
in-process webhook receiver for the webhook-delivery test."""
from __future__ import annotations

import http.server
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
API_KEY = "test-secret-key"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def server(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("cfb")
    port = _free_port()
    env = {
        **os.environ,
        "CONTEXTUAL_DATA_DIR": str(data_dir),
        "CONTEXTUAL_PORT": str(port),
        "API_KEY": API_KEY,
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "contextual_feedback"],
        cwd=PROJECT_ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 30
    try:
        while time.time() < deadline:
            try:
                if requests.get(f"{base}/api/v1/health", timeout=1).status_code == 200:
                    break
            except requests.RequestException:
                time.sleep(0.3)
        else:
            raise RuntimeError("server did not start")
        yield {"base": base, "data_dir": data_dir}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture
def base(server):
    return server["base"]


@pytest.fixture
def data_dir(server):
    return server["data_dir"]


@pytest.fixture
def auth():
    return {"X-API-Key": API_KEY}


class _WebhookReceiver:
    def __init__(self):
        self.received: list[dict] = []


@pytest.fixture
def webhook():
    """Start a localhost HTTP server that records POSTed JSON bodies."""
    state = _WebhookReceiver()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                state.received.append(json.loads(body))
            except Exception:
                state.received.append({"_raw": body.decode("utf-8", "replace")})
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args):  # silence
            pass

    port = _free_port()
    httpd = http.server.HTTPServer(("127.0.0.1", port), Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield {"url": f"http://127.0.0.1:{port}/hook", "state": state}
    finally:
        httpd.shutdown()


def upload_txt(base, auth, text="the alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima", name="doc.txt"):
    resp = requests.post(
        f"{base}/api/v1/documents",
        headers=auth,
        files={"file": (name, text.encode(), "text/plain")},
        timeout=15,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def make_context(base, auth, **overrides):
    payload = {"max_tokens": 1000, "overlap_tokens": 50, "temperature": 0.5}
    payload.update(overrides)
    resp = requests.post(f"{base}/api/v1/context", headers=auth, json=payload, timeout=10)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def wait_for_job(base, auth, job_id, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = requests.get(f"{base}/api/v1/jobs/{job_id}", headers=auth, timeout=10).json()
        if body["status"] == "completed":
            return body
        time.sleep(0.2)
    raise AssertionError(f"job {job_id} did not complete")
