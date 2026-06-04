"""Behavioural contract tests for the contextual-feedback API."""
from __future__ import annotations

import requests
from conftest import make_context, upload_txt, wait_for_job


# --- health + auth ----------------------------------------------------------- #
def test_health_no_auth(base):
    r = requests.get(f"{base}/api/v1/health", timeout=10)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "healthy" and "count" in body


def test_auth_required(base):
    assert requests.get(f"{base}/api/v1/documents", timeout=10).status_code == 401
    assert requests.get(f"{base}/api/v1/documents", headers={"X-API-Key": "wrong"}, timeout=10).status_code == 401


def test_error_shape_is_json_object(base):
    r = requests.get(f"{base}/api/v1/documents", timeout=10)  # 401
    assert isinstance(r.json().get("error"), str)


# --- documents --------------------------------------------------------------- #
def test_upload_txt(base, auth):
    r = requests.post(
        f"{base}/api/v1/documents", headers=auth,
        files={"file": ("notes.txt", b"hello world from a document", "text/plain")}, timeout=15,
    )
    assert r.status_code == 201
    body = r.json()
    assert body["file_type"] == "txt"  # lowercase bare extension, no dot
    assert {"id", "filename", "size_bytes", "uploaded_at"} <= set(body)


def test_upload_rejects_unsupported_type(base, auth):
    r = requests.post(
        f"{base}/api/v1/documents", headers=auth,
        files={"file": ("data.csv", b"a,b,c", "text/csv")}, timeout=10,
    )
    assert r.status_code == 400
    assert "error" in r.json()


def test_upload_rejects_empty_file(base, auth):
    r = requests.post(
        f"{base}/api/v1/documents", headers=auth,
        files={"file": ("empty.txt", b"", "text/plain")}, timeout=10,
    )
    assert r.status_code == 400


def test_upload_rejects_oversize(base, auth):
    big = b"x" * (10 * 1024 * 1024 + 1)
    r = requests.post(
        f"{base}/api/v1/documents", headers=auth,
        files={"file": ("big.txt", big, "text/plain")}, timeout=30,
    )
    assert r.status_code == 400


def test_get_and_delete_document(base, auth):
    doc_id = upload_txt(base, auth)
    got = requests.get(f"{base}/api/v1/documents/{doc_id}", headers=auth, timeout=10)
    assert got.status_code == 200 and got.json()["id"] == doc_id
    assert requests.delete(f"{base}/api/v1/documents/{doc_id}", headers=auth, timeout=10).status_code == 204
    assert requests.get(f"{base}/api/v1/documents/{doc_id}", headers=auth, timeout=10).status_code == 404


def test_get_document_404(base, auth):
    assert requests.get(f"{base}/api/v1/documents/999999", headers=auth, timeout=10).status_code == 404


def test_documents_list_is_bare_array(base, auth):
    upload_txt(base, auth)
    r = requests.get(f"{base}/api/v1/documents", headers=auth, timeout=10)
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_documents_pagination(base, auth):
    for _ in range(3):
        upload_txt(base, auth)
    r = requests.get(f"{base}/api/v1/documents?page=1&per_page=2", headers=auth, timeout=10)
    assert r.status_code == 200 and len(r.json()) <= 2


# --- context configs --------------------------------------------------------- #
def test_create_context(base, auth):
    r = requests.post(
        f"{base}/api/v1/context", headers=auth,
        json={"max_tokens": 2000, "overlap_tokens": 100, "temperature": 0.3}, timeout=10,
    )
    assert r.status_code == 201
    body = r.json()
    assert {"id", "max_tokens", "overlap_tokens", "temperature", "created_at"} <= set(body)


def test_context_validation(base, auth):
    bad = [
        {"max_tokens": 50, "overlap_tokens": 10, "temperature": 0.5},      # max_tokens too low
        {"max_tokens": 999999, "overlap_tokens": 10, "temperature": 0.5},  # too high
        {"max_tokens": 1000, "overlap_tokens": -1, "temperature": 0.5},    # overlap < 0
        {"max_tokens": 1000, "overlap_tokens": 9999, "temperature": 0.5},  # overlap too high
        {"max_tokens": 1000, "overlap_tokens": 10, "temperature": 5.0},    # temp > 1
    ]
    for payload in bad:
        assert requests.post(f"{base}/api/v1/context", headers=auth, json=payload, timeout=10).status_code == 400


def test_context_webhook_url_optional_and_typed(base, auth):
    ok = requests.post(
        f"{base}/api/v1/context", headers=auth,
        json={"max_tokens": 1000, "overlap_tokens": 50, "temperature": 0.5, "webhook_url": "http://x/y"}, timeout=10,
    )
    assert ok.status_code == 201 and ok.json()["webhook_url"] == "http://x/y"
    bad = requests.post(
        f"{base}/api/v1/context", headers=auth,
        json={"max_tokens": 1000, "overlap_tokens": 50, "temperature": 0.5, "webhook_url": 123}, timeout=10,
    )
    assert bad.status_code == 400


def test_get_and_update_context(base, auth):
    ctx_id = make_context(base, auth)
    assert requests.get(f"{base}/api/v1/context/{ctx_id}", headers=auth, timeout=10).status_code == 200
    upd = requests.put(
        f"{base}/api/v1/context/{ctx_id}", headers=auth,
        json={"max_tokens": 4000, "overlap_tokens": 200, "temperature": 0.9}, timeout=10,
    )
    assert upd.status_code == 200 and upd.json()["max_tokens"] == 4000
    assert requests.get(f"{base}/api/v1/context/999999", headers=auth, timeout=10).status_code == 404
    assert requests.put(
        f"{base}/api/v1/context/999999", headers=auth,
        json={"max_tokens": 1000, "overlap_tokens": 50, "temperature": 0.5}, timeout=10,
    ).status_code == 404


# --- async analysis ---------------------------------------------------------- #
def test_analysis_async_flow_and_result_shape(base, auth):
    text = " ".join(f"word{i}" for i in range(300))
    doc_id = upload_txt(base, auth, text=text)
    ctx_id = make_context(base, auth, max_tokens=100, overlap_tokens=20)

    start = requests.post(
        f"{base}/api/v1/analysis", headers=auth,
        json={"document_id": doc_id, "context_id": ctx_id}, timeout=10,
    )
    assert start.status_code == 202
    job = start.json()
    assert job["status"] == "pending"

    done = wait_for_job(base, auth, job["id"])
    assert done["result_id"] is not None
    assert done["completed_at"]

    result = requests.get(f"{base}/api/v1/results/{done['result_id']}", headers=auth, timeout=10).json()
    assert {
        "id", "document_id", "context_id", "chunk_count", "tokens_processed",
        "per_chunk_results", "aggregated_keywords", "completed_at",
    } <= set(result)
    assert result["tokens_processed"] == 300            # total word count pre-chunking
    assert result["chunk_count"] == len(result["per_chunk_results"])
    assert len(result["aggregated_keywords"]) <= 10
    for chunk in result["per_chunk_results"]:
        assert {"chunk_index", "word_count", "char_count", "top_keywords"} <= set(chunk)
        assert len(chunk["top_keywords"]) <= 5


def test_analysis_missing_ids_404(base, auth):
    assert requests.post(
        f"{base}/api/v1/analysis", headers=auth, json={"document_id": 999999, "context_id": 999999}, timeout=10
    ).status_code == 404


def test_jobs_and_results_lists_are_bare_arrays(base, auth):
    doc_id = upload_txt(base, auth)
    ctx_id = make_context(base, auth)
    job = requests.post(
        f"{base}/api/v1/analysis", headers=auth, json={"document_id": doc_id, "context_id": ctx_id}, timeout=10
    ).json()
    wait_for_job(base, auth, job["id"])
    assert isinstance(requests.get(f"{base}/api/v1/jobs", headers=auth, timeout=10).json(), list)
    assert isinstance(requests.get(f"{base}/api/v1/results", headers=auth, timeout=10).json(), list)
    assert requests.get(f"{base}/api/v1/jobs/999999", headers=auth, timeout=10).status_code == 404
    assert requests.get(f"{base}/api/v1/results/999999", headers=auth, timeout=10).status_code == 404


# --- webhook delivery -------------------------------------------------------- #
def test_webhook_delivered(base, auth, webhook):
    doc_id = upload_txt(base, auth)
    ctx_id = make_context(base, auth, webhook_url=webhook["url"])
    job = requests.post(
        f"{base}/api/v1/analysis", headers=auth, json={"document_id": doc_id, "context_id": ctx_id}, timeout=10
    ).json()
    wait_for_job(base, auth, job["id"])
    # Give the webhook thread a moment to deliver.
    import time
    for _ in range(25):
        if webhook["state"].received:
            break
        time.sleep(0.2)
    assert webhook["state"].received, "webhook was not delivered"
    payload = webhook["state"].received[0]
    assert "per_chunk_results" in payload and "aggregated_keywords" in payload
