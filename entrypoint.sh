#!/bin/bash
set -e

# Block all new outbound connections at the kernel level.
# Set DISABLE_NETWORK_ISOLATION=1 to skip (e.g. Docker Desktop on macOS/Windows
# where NET_ADMIN may not be available). Only do this if you accept that egress
# is then enforced only by the telemetry opt-out env vars, not iptables.
if [ "${DISABLE_NETWORK_ISOLATION:-0}" = "1" ]; then
    echo "[security] WARNING: Network isolation disabled via DISABLE_NETWORK_ISOLATION=1."
    echo "[security] Egress is NOT blocked — relying on telemetry opt-out env vars only."
else
    iptables -A OUTPUT -o lo -j ACCEPT
    iptables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
    iptables -A OUTPUT -j DROP
    echo "[security] Network egress blocked via iptables."
fi

echo "Starting Ollama..."
ollama serve &

echo "Waiting for Ollama to be ready..."
until curl -sf http://localhost:11434/api/tags > /dev/null 2>&1; do
    sleep 1
done
echo "Ollama ready."

echo "Starting Zotero Semantic Search on http://0.0.0.0:8000"
exec pixi run uvicorn main:app --host 0.0.0.0 --port 8000 --log-level info
