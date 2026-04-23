#!/bin/bash
set -e

# Block all new outbound connections at the kernel level.
# ESTABLISHED,RELATED allows the container to respond to inbound connections
# (e.g. port 8000 from the host) and loopback allows Ollama <-> app traffic.
iptables -A OUTPUT -o lo -j ACCEPT
iptables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables -A OUTPUT -j DROP

echo "Starting Ollama..."
ollama serve &

echo "Waiting for Ollama to be ready..."
until curl -sf http://localhost:11434/api/tags > /dev/null 2>&1; do
    sleep 1
done
echo "Ollama ready."

echo "Starting Zotero Semantic Search on http://0.0.0.0:8000"
exec pixi run uvicorn main:app --host 0.0.0.0 --port 8000 --log-level info
