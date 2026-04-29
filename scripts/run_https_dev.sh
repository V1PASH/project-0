#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CERT_DIR="${ROOT_DIR}/certs"
CERT_FILE="${CERT_DIR}/localhost.crt"
KEY_FILE="${CERT_DIR}/localhost.key"

mkdir -p "${CERT_DIR}"

if [[ ! -f "${CERT_FILE}" || ! -f "${KEY_FILE}" ]]; then
  echo "Generating self-signed localhost certificate..."
  openssl req \
    -x509 \
    -newkey rsa:2048 \
    -sha256 \
    -days 365 \
    -nodes \
    -keyout "${KEY_FILE}" \
    -out "${CERT_FILE}" \
    -subj "/CN=localhost" \
    -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"
fi

echo "Starting HTTPS signaling server on https://localhost:8443"
source "${ROOT_DIR}/venv/bin/activate"
uvicorn signaling.server:app \
  --host 0.0.0.0 \
  --port 8443 \
  --reload \
  --ssl-certfile "${CERT_FILE}" \
  --ssl-keyfile "${KEY_FILE}"
