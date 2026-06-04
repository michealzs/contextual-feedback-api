#!/usr/bin/env bash
set -euo pipefail

DATA_DIR="${CONTEXTUAL_DATA_DIR:-/app}"
mkdir -p "$DATA_DIR"/data "$DATA_DIR"/uploads "$DATA_DIR"/logs

echo "[entrypoint] data dir: $DATA_DIR"
echo "[entrypoint] starting: $*"
exec "$@"
