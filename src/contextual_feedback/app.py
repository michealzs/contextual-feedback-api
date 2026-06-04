import json
import os
import sqlite3
import threading
from collections import Counter
from datetime import datetime

import pdfplumber
import requests
from flask import Flask, g, jsonify, request

app = Flask(__name__)

try:
    from prometheus_flask_exporter import PrometheusMetrics

    _metrics = PrometheusMetrics(app, group_by="endpoint")
except Exception:  # pragma: no cover - metrics are best-effort
    _metrics = None

BASE_DIR = os.environ.get("CONTEXTUAL_DATA_DIR", "/app")
DATA_DIR = os.path.join(BASE_DIR, "data")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
UPLOADS_DIR = os.path.join(BASE_DIR, "uploads")
for _d in (DATA_DIR, LOGS_DIR, UPLOADS_DIR):
    os.makedirs(_d, exist_ok=True)
log_path = os.path.join(LOGS_DIR, "api.log")
DB_PATH = os.path.join(DATA_DIR, "documents.db")
MAX_FILE_SIZE = 10 * 1024 * 1024

API_KEY = os.environ.get("API_KEY", "terminus-dev-key")

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has",
    "he", "in", "is", "it", "its", "of", "on", "that", "the", "to", "was",
    "will", "with", "i", "you", "this", "but", "they", "have", "had", "what",
    "said", "each", "which", "she", "do", "how", "their", "if", "up", "out",
    "many", "then", "them", "these", "so", "some", "her", "would", "make",
    "like", "into", "him", "two", "more", "very", "after", "words",
    "just", "where", "most", "get", "through", "back", "much", "before",
    "go", "good", "new", "write", "our", "me", "too", "any", "day", "same",
    "right", "look", "think", "also", "around", "another", "came", "come",
    "work", "three", "must", "because", "does", "part", "even", "place",
    "well", "such", "here", "take", "why", "things", "help", "put", "years",
    "different", "away", "again", "off", "went", "old", "number", "great",
    "tell", "men", "say", "small", "every", "found", "still", "between",
    "name", "should", "home", "big", "give", "air", "line", "set", "own",
    "under", "read", "last", "never", "us", "left", "end", "along", "while",
    "might", "next", "sound", "below", "saw", "something", "thought", "both",
    "few", "those", "always", "show", "large", "often", "together", "asked",
    "house", "don't", "world", "going", "want", "school", "important",
    "until", "form", "food", "keep", "children", "feet", "land", "side",
    "without", "boy", "once", "enough", "almost", "general", "during",
    "example", "whether", "later", "open", "order", "group", "problem",
    "however", "run", "young", "close", "case", "force", "whole", "against",
    "become", "fact", "nothing", "public", "less", "able", "pay", "let",
    "could", "state", "person", "since", "second", "late",
    "present", "several", "better", "given", "hand", "high", "sure",
    "upon", "head", "move", "five",
}


def init_db():
    os.makedirs("/app/data", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            file_type TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            uploaded_at TEXT NOT NULL
        )
        """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS context_configs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            max_tokens INTEGER NOT NULL,
            overlap_tokens INTEGER NOT NULL,
            temperature REAL NOT NULL,
            webhook_url TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS analysis_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id INTEGER NOT NULL,
            context_id INTEGER NOT NULL,
            chunk_count INTEGER NOT NULL,
            tokens_processed INTEGER NOT NULL,
            per_chunk_results TEXT NOT NULL,
            aggregated_keywords TEXT NOT NULL,
            completed_at TEXT NOT NULL
        )
        """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id INTEGER NOT NULL,
            context_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            result_id INTEGER,
            created_at TEXT NOT NULL,
            completed_at TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def get_client_ip():
    if request.headers.get("X-Forwarded-For"):
        return request.headers.get("X-Forwarded-For").split(",")[0].strip()
    return request.remote_addr


@app.before_request
def before_request():
    g.request_id = request.headers.get("X-Request-ID")
    if request.path in ("/api/v1/health", "/metrics"):
        return
    key = request.headers.get("X-API-Key")
    if key != API_KEY:
        return jsonify({"error": "Unauthorized"}), 401


@app.after_request
def after_request(response):
    if hasattr(g, "request_id") and g.request_id:
        response.headers["X-Request-ID"] = g.request_id
    entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "method": request.method,
        "path": request.path,
        "status": response.status_code,
        "client_ip": get_client_ip(),
        "request_id": getattr(g, "request_id", None),
    }
    with open(log_path, "a") as f:
        f.write(json.dumps(entry) + "\n")
    return response


def validate_document(file):
    if not file or file.filename == "":
        return "No file provided"
    if "." not in file.filename:
        return "Invalid filename"
    ext = file.filename.rsplit(".", 1)[1].lower()
    if ext not in ("pdf", "txt"):
        return "Only .pdf and .txt files are allowed"
    file.seek(0, 2)
    size = file.tell()
    file.seek(0)
    if size == 0:
        return "File is empty"
    if size > MAX_FILE_SIZE:
        return "File exceeds 10 MB limit"
    return None


def extract_text(file_path, file_type):
    if file_type == "txt":
        with open(file_path, encoding="utf-8") as f:
            return f.read()
    elif file_type == "pdf":
        text = ""
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
        return text
    return ""


def get_top_keywords(words, n=5):
    filtered = [
        w.lower()
        for w in words
        if w.isalpha() and w.lower() not in STOPWORDS and len(w) > 2
    ]
    if not filtered:
        return []
    return [w for w, _ in Counter(filtered).most_common(n)]


def run_mock_analysis(text, max_tokens, overlap_tokens):
    words = text.split()
    chunk_size = max_tokens
    step = max(chunk_size - overlap_tokens, 1)
    chunks = []
    for i in range(0, len(words), step):
        chunk = words[i : i + chunk_size]
        if not chunk:
            break
        chunks.append(chunk)
        if i + chunk_size >= len(words):
            break

    per_chunk_results = []
    all_keywords = []
    for idx, chunk in enumerate(chunks):
        word_count = len(chunk)
        char_count = sum(len(w) for w in chunk)
        keywords = get_top_keywords(chunk, 5)
        per_chunk_results.append(
            {
                "chunk_index": idx,
                "word_count": word_count,
                "char_count": char_count,
                "top_keywords": keywords,
            }
        )
        all_keywords.extend(keywords)

    aggregated = [w for w, _ in Counter(all_keywords).most_common(10)]
    return {
        "chunk_count": len(chunks),
        "tokens_processed": len(words),
        "per_chunk_results": per_chunk_results,
        "aggregated_keywords": aggregated,
    }


def result_to_dict(row):
    return {
        "id": row[0],
        "document_id": row[1],
        "context_id": row[2],
        "chunk_count": row[3],
        "tokens_processed": row[4],
        "per_chunk_results": json.loads(row[5]),
        "aggregated_keywords": json.loads(row[6]),
        "completed_at": row[7],
    }


def job_to_dict(row):
    return {
        "id": row[0],
        "document_id": row[1],
        "context_id": row[2],
        "status": row[3],
        "result_id": row[4],
        "created_at": row[5],
        "completed_at": row[6],
    }


def process_analysis_job(job_id, document_id, context_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT file_type FROM documents WHERE id = ?", (document_id,)
    )
    doc_row = c.fetchone()
    if not doc_row:
        conn.close()
        return
    file_type = doc_row[0]

    c.execute(
        "SELECT max_tokens, overlap_tokens, temperature, webhook_url FROM context_configs WHERE id = ?",
        (context_id,),
    )
    ctx_row = c.fetchone()
    if not ctx_row:
        conn.close()
        return
    max_tokens, overlap_tokens, temperature, webhook_url = ctx_row

    file_path = os.path.join(UPLOADS_DIR, f"{document_id}.{file_type}")
    if not os.path.exists(file_path):
        conn.close()
        return
    try:
        text = extract_text(file_path, file_type)
    except Exception:
        conn.close()
        return

    result = run_mock_analysis(text, max_tokens, overlap_tokens)
    completed_at = datetime.utcnow().isoformat()
    c.execute(
        "INSERT INTO analysis_results (document_id, context_id, chunk_count, tokens_processed, per_chunk_results, aggregated_keywords, completed_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            document_id,
            context_id,
            result["chunk_count"],
            result["tokens_processed"],
            json.dumps(result["per_chunk_results"]),
            json.dumps(result["aggregated_keywords"]),
            completed_at,
        ),
    )
    result_id = c.lastrowid
    c.execute(
        "UPDATE jobs SET status = ?, result_id = ?, completed_at = ? WHERE id = ?",
        ("completed", result_id, completed_at, job_id),
    )
    conn.commit()
    conn.close()

    if webhook_url:
        try:
            full_result = result.copy()
            full_result["id"] = result_id
            full_result["document_id"] = document_id
            full_result["context_id"] = context_id
            full_result["completed_at"] = completed_at
            requests.post(webhook_url, json=full_result, timeout=5)
        except Exception:
            pass


@app.route("/api/v1/documents", methods=["POST"])
def upload_document():
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400
    file = request.files["file"]
    error = validate_document(file)
    if error:
        return jsonify({"error": error}), 400
    ext = file.filename.rsplit(".", 1)[1].lower()
    uploaded_at = datetime.utcnow().isoformat()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT INTO documents (filename, file_type, size_bytes, uploaded_at) VALUES (?, ?, ?, ?)",
        (file.filename, ext, file.seek(0, 2), uploaded_at),
    )
    doc_id = c.lastrowid
    conn.commit()
    conn.close()
    file.seek(0)
    file.save(os.path.join(UPLOADS_DIR, f"{doc_id}.{ext}"))
    return jsonify(
        {
            "id": doc_id,
            "filename": file.filename,
            "file_type": ext,
            "size_bytes": file.seek(0, 2),
            "uploaded_at": uploaded_at,
        }
    ), 201


@app.route("/api/v1/documents", methods=["GET"])
def list_documents():
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 10, type=int)
    if page < 1:
        page = 1
    if per_page < 1:
        per_page = 1
    if per_page > 100:
        per_page = 100
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT id, filename, file_type, size_bytes, uploaded_at FROM documents ORDER BY id DESC LIMIT ? OFFSET ?",
        (per_page, (page - 1) * per_page),
    )
    rows = c.fetchall()
    conn.close()
    return jsonify(
        [
            {
                "id": r[0],
                "filename": r[1],
                "file_type": r[2],
                "size_bytes": r[3],
                "uploaded_at": r[4],
            }
            for r in rows
        ]
    ), 200


@app.route("/api/v1/documents/<int:doc_id>", methods=["GET"])
def get_document(doc_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT id, filename, file_type, size_bytes, uploaded_at FROM documents WHERE id = ?",
        (doc_id,),
    )
    row = c.fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "Document not found"}), 404
    return jsonify(
        {
            "id": row[0],
            "filename": row[1],
            "file_type": row[2],
            "size_bytes": row[3],
            "uploaded_at": row[4],
        }
    ), 200


@app.route("/api/v1/documents/<int:doc_id>", methods=["DELETE"])
def delete_document(doc_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT file_type FROM documents WHERE id = ?", (doc_id,)
    )
    row = c.fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "Document not found"}), 404
    file_type = row[0]
    c.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
    conn.commit()
    conn.close()
    file_path = os.path.join(UPLOADS_DIR, f"{doc_id}.{file_type}")
    if os.path.exists(file_path):
        os.remove(file_path)
    return "", 204


@app.route("/api/v1/context", methods=["POST"])
def create_context():
    if not request.is_json:
        return jsonify({"error": "Invalid JSON"}), 400
    data = request.get_json(silent=True)
    if data is None:
        return jsonify({"error": "Invalid JSON"}), 400
    if not isinstance(data, dict):
        return jsonify({"error": "Expected JSON object"}), 400
    max_tokens = data.get("max_tokens")
    overlap_tokens = data.get("overlap_tokens")
    temperature = data.get("temperature")
    webhook_url = data.get("webhook_url")
    if (
        not isinstance(max_tokens, int)
        or not isinstance(overlap_tokens, int)
        or not isinstance(temperature, (int, float))
    ):
        return jsonify({"error": "Invalid field types"}), 400
    if max_tokens < 100 or max_tokens > 16000:
        return jsonify({"error": "max_tokens must be 100-16000"}), 400
    if overlap_tokens < 0 or overlap_tokens > 500:
        return jsonify({"error": "overlap_tokens must be 0-500"}), 400
    if temperature < 0.0 or temperature > 1.0:
        return jsonify({"error": "temperature must be 0.0-1.0"}), 400
    if webhook_url is not None and not isinstance(webhook_url, str):
        return jsonify({"error": "webhook_url must be a string"}), 400
    created_at = datetime.utcnow().isoformat()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT INTO context_configs (max_tokens, overlap_tokens, temperature, webhook_url, created_at) VALUES (?, ?, ?, ?, ?)",
        (max_tokens, overlap_tokens, temperature, webhook_url, created_at),
    )
    ctx_id = c.lastrowid
    conn.commit()
    conn.close()
    result = {
        "id": ctx_id,
        "max_tokens": max_tokens,
        "overlap_tokens": overlap_tokens,
        "temperature": temperature,
        "created_at": created_at,
    }
    if webhook_url is not None:
        result["webhook_url"] = webhook_url
    return jsonify(result), 201


@app.route("/api/v1/context/<int:ctx_id>", methods=["GET"])
def get_context(ctx_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT id, max_tokens, overlap_tokens, temperature, webhook_url, created_at FROM context_configs WHERE id = ?",
        (ctx_id,),
    )
    row = c.fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "Context config not found"}), 404
    result = {
        "id": row[0],
        "max_tokens": row[1],
        "overlap_tokens": row[2],
        "temperature": row[3],
        "created_at": row[5],
    }
    if row[4] is not None:
        result["webhook_url"] = row[4]
    return jsonify(result), 200


@app.route("/api/v1/analysis", methods=["POST"])
def run_analysis():
    if not request.is_json:
        return jsonify({"error": "Invalid JSON"}), 400
    data = request.get_json(silent=True)
    if data is None:
        return jsonify({"error": "Invalid JSON"}), 400
    document_id = data.get("document_id")
    context_id = data.get("context_id")
    if not isinstance(document_id, int) or not isinstance(context_id, int):
        return jsonify({"error": "document_id and context_id must be integers"}), 400
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT id FROM documents WHERE id = ?", (document_id,)
    )
    doc_row = c.fetchone()
    if not doc_row:
        conn.close()
        return jsonify({"error": "Document not found"}), 404
    c.execute(
        "SELECT id FROM context_configs WHERE id = ?", (context_id,)
    )
    ctx_row = c.fetchone()
    if not ctx_row:
        conn.close()
        return jsonify({"error": "Context config not found"}), 404
    created_at = datetime.utcnow().isoformat()
    c.execute(
        "INSERT INTO jobs (document_id, context_id, status, created_at) VALUES (?, ?, ?, ?)",
        (document_id, context_id, "pending", created_at),
    )
    job_id = c.lastrowid
    conn.commit()
    conn.close()
    thread = threading.Thread(
        target=process_analysis_job,
        args=(job_id, document_id, context_id),
    )
    thread.daemon = True
    thread.start()
    return jsonify({"id": job_id, "status": "pending"}), 202


@app.route("/api/v1/jobs/<int:job_id>", methods=["GET"])
def get_job(job_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT id, document_id, context_id, status, result_id, created_at, completed_at FROM jobs WHERE id = ?",
        (job_id,),
    )
    row = c.fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job_to_dict(row)), 200


@app.route("/api/v1/jobs", methods=["GET"])
def list_jobs():
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 10, type=int)
    if page < 1:
        page = 1
    if per_page < 1:
        per_page = 1
    if per_page > 100:
        per_page = 100
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT id, document_id, context_id, status, result_id, created_at, completed_at FROM jobs ORDER BY id DESC LIMIT ? OFFSET ?",
        (per_page, (page - 1) * per_page),
    )
    rows = c.fetchall()
    conn.close()
    return jsonify([job_to_dict(r) for r in rows]), 200


@app.route("/api/v1/context/<int:ctx_id>", methods=["PUT"])
def update_context(ctx_id):
    if not request.is_json:
        return jsonify({"error": "Invalid JSON"}), 400
    data = request.get_json(silent=True)
    if data is None:
        return jsonify({"error": "Invalid JSON"}), 400
    if not isinstance(data, dict):
        return jsonify({"error": "Expected JSON object"}), 400
    max_tokens = data.get("max_tokens")
    overlap_tokens = data.get("overlap_tokens")
    temperature = data.get("temperature")
    webhook_url = data.get("webhook_url")
    if (
        not isinstance(max_tokens, int)
        or not isinstance(overlap_tokens, int)
        or not isinstance(temperature, (int, float))
    ):
        return jsonify({"error": "Invalid field types"}), 400
    if max_tokens < 100 or max_tokens > 16000:
        return jsonify({"error": "max_tokens must be 100-16000"}), 400
    if overlap_tokens < 0 or overlap_tokens > 500:
        return jsonify({"error": "overlap_tokens must be 0-500"}), 400
    if temperature < 0.0 or temperature > 1.0:
        return jsonify({"error": "temperature must be 0.0-1.0"}), 400
    if webhook_url is not None and not isinstance(webhook_url, str):
        return jsonify({"error": "webhook_url must be a string"}), 400
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT id FROM context_configs WHERE id = ?", (ctx_id,)
    )
    row = c.fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "Context config not found"}), 404
    c.execute(
        "UPDATE context_configs SET max_tokens = ?, overlap_tokens = ?, temperature = ?, webhook_url = ? WHERE id = ?",
        (max_tokens, overlap_tokens, temperature, webhook_url, ctx_id),
    )
    conn.commit()
    c.execute(
        "SELECT id, max_tokens, overlap_tokens, temperature, webhook_url, created_at FROM context_configs WHERE id = ?",
        (ctx_id,),
    )
    row = c.fetchone()
    conn.close()
    result = {
        "id": row[0],
        "max_tokens": row[1],
        "overlap_tokens": row[2],
        "temperature": row[3],
        "created_at": row[5],
    }
    if row[4] is not None:
        result["webhook_url"] = row[4]
    return jsonify(result), 200


@app.route("/api/v1/results", methods=["GET"])
def list_results():
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 10, type=int)
    if page < 1:
        page = 1
    if per_page < 1:
        per_page = 1
    if per_page > 100:
        per_page = 100
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT * FROM analysis_results ORDER BY id DESC LIMIT ? OFFSET ?",
        (per_page, (page - 1) * per_page),
    )
    rows = c.fetchall()
    conn.close()
    return jsonify([result_to_dict(r) for r in rows]), 200


@app.route("/api/v1/results/<int:result_id>", methods=["GET"])
def get_result(result_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT * FROM analysis_results WHERE id = ?", (result_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "Result not found"}), 404
    return jsonify(result_to_dict(row)), 200


@app.route("/api/v1/health", methods=["GET"])
def health():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM documents")
    count = c.fetchone()[0]
    conn.close()
    return jsonify({"status": "healthy", "count": count}), 200


@app.errorhandler(500)
def internal_error(error):
    return jsonify({"error": "Internal server error"}), 500


init_db()


def main():
    port = int(os.environ.get("CONTEXTUAL_PORT", "5000"))
    app.run(host=os.environ.get("CONTEXTUAL_HOST", "0.0.0.0"), port=port)


if __name__ == "__main__":
    main()
