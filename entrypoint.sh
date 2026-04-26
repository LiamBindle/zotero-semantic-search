#!/bin/bash
set -e

echo "[security] Network egress blocked via Docker internal network."

echo "Starting Ollama..."
ollama serve &

echo "Waiting for Ollama to be ready..."
until curl -sf http://localhost:11434/api/tags > /dev/null 2>&1; do
    sleep 1
done
echo "Ollama ready."

echo "Starting Zotero Private Search on http://0.0.0.0:8765"
exec pixi run uvicorn main:app --host 0.0.0.0 --port 8765 --log-level info
