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

    # Verify the rules actually take effect. TCP probe to 1.1.1.1:443 — no DNS,
    # no payload, just a SYN. If iptables drops it we time out (good). If it
    # connects, the airgap is NOT in effect and we say so loudly.
    if curl -sf --max-time 3 -o /dev/null https://1.1.1.1/ 2>/dev/null; then
        echo "[security] WARNING: egress probe to 1.1.1.1 succeeded — airgap is NOT in effect."
    else
        echo "[security] Egress probe blocked — airgap verified."
    fi
fi

echo "Starting Ollama..."
ollama serve &

echo "Waiting for Ollama to be ready..."
until curl -sf http://localhost:11434/api/tags > /dev/null 2>&1; do
    sleep 1
done
echo "Ollama ready."

echo "Starting Zotero Private Search on http://0.0.0.0:8765"
exec pixi run uvicorn main:app --host 0.0.0.0 --port 8765 --log-level info
