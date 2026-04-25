# Security & Threat Model

Zotero Private Search is built for researchers whose work requires a
*verifiable* guarantee that documents never leave their machine. This
document describes what the privacy guarantee actually covers, how to
verify it, and what is explicitly out of scope.

## What the privacy guarantee covers

When the container is run on Linux with the `NET_ADMIN` capability (the
default in `docker-compose.yml`), the entrypoint applies the following
iptables rules **before** any application code starts:

```bash
iptables -A OUTPUT -o lo -j ACCEPT
iptables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables -A OUTPUT -j DROP
```

This is the entire egress policy:

1. Loopback traffic (between the FastAPI app and the local Ollama
   instance, both inside the container) is permitted.
2. Reply packets on already-established inbound connections are
   permitted, so the UI on port 8765 stays reachable.
3. Everything else outbound is dropped.

Because this is enforced in the kernel's netfilter layer, **no userspace
process inside the container can bypass it** — not the embedding model,
not Ollama, not a future Python dependency that decides to phone home,
not a malicious package that sneaks into the supply chain after the
image is built.

In addition, defense-in-depth measures are applied:

- All model weights are baked into the image at build time (Dockerfile
  pre-pulls Ollama and fastembed models). A running container has no
  reason to make a network request.
- Telemetry is disabled in every component via environment variables:
  `ANONYMIZED_TELEMETRY=false`, `HF_HUB_OFFLINE=1`,
  `TRANSFORMERS_OFFLINE=1`, `OLLAMA_NO_ANALYTICS=1`, `DO_NOT_TRACK=1`.
- The Zotero library is bind-mounted **read-only** into the container.
- The application server binds to `127.0.0.1` (localhost) inside the
  container; only the explicit Docker port mapping exposes it to the
  host.

## Verifying the guarantee yourself

The fact that you do not have to trust this README is the point. Run
these checks against your own running container.

### 1. Confirm outbound traffic is blocked

```bash
docker compose exec zotero-private-search curl -s --max-time 5 https://example.com
# Expected: connection times out, curl exits non-zero
```

### 2. Inspect the kernel filter table directly

```bash
docker compose exec zotero-private-search iptables -L OUTPUT -n -v
# Expected: ACCEPT on lo, ACCEPT for ESTABLISHED,RELATED, DROP for the rest.
# The DROP rule should show non-zero packet/byte counters after some
# attempted egress, confirming it is actually catching traffic.
```

### 3. Confirm the entrypoint applied the rules at startup

```bash
docker compose logs zotero-private-search | grep '\[security\]'
# Expected on Linux: "[security] Network egress blocked via iptables."
# Expected on Docker Desktop: "[security] WARNING: Network isolation
# disabled via DISABLE_NETWORK_ISOLATION=1."
```

### 4. Audit the source

The source is AGPL-3.0. The relevant files are short and self-contained:

- [`entrypoint.sh`](entrypoint.sh) — the iptables rules
- [`Dockerfile`](Dockerfile) — model bake-in, telemetry env vars
- [`pyproject.toml`](pyproject.toml) — telemetry env vars for the dev
  environment
- [`main.py`](main.py) — every network-touching code path

If your IT department, ethics board, or collaborators want independent
verification, they can read these files end to end in well under an
hour.

## Out of scope

Be honest about what this tool does **not** protect against.

### macOS / Windows: kernel block is unavailable

Docker Desktop on macOS and Windows runs containers inside a managed
Linux VM and does not expose the `NET_ADMIN` capability needed to run
`iptables` against the real host network stack. On these platforms the
desktop launcher generates a `docker-compose.yml` with
`DISABLE_NETWORK_ISOLATION=1` and the container falls back to the
telemetry opt-out env vars.

In practice that still means there is no code in this project that
sends your documents anywhere. But it is a "no code does X" guarantee,
not a "the kernel will physically prevent X" guarantee. **If verifiable
network isolation is the reason you chose this tool, run it on Linux.**

### Physical access

A user with physical access to the host machine can read `~/Zotero`,
read the on-disk vector index (`CHROMA_PATH`), or take a screenshot of
the running app. This tool does not encrypt your library at rest — that
is your operating system's job. Use full-disk encryption.

### Host-level shell access

A user who can `docker exec` into the container, run arbitrary commands
as root on the host, or modify the image before it starts can do
anything they want, including disabling the iptables rules. The threat
model assumes the host is a workstation that *you* control.

### Supply-chain attacks on the base image and dependencies

Docker base images, Ollama, fastembed weights, and Python packages are
pinned to specific versions, but they are not signed end-to-end. A
sufficiently motivated attacker who compromises an upstream registry
could ship a malicious build. The kernel-level egress block is
specifically designed to mitigate this — even a backdoored dependency
cannot exfiltrate data — but it does not eliminate the risk of, for
example, code that corrupts the local index or causes a crash. Audit
upstream tags before deploying in high-stakes settings.

### Side channels

DNS lookups (which would be blocked anyway), clock skew, container
metrics, host-side timing observations, and similar low-bandwidth side
channels are not addressed. The tool is designed to defeat a
"my-dependency-phoned-home" failure mode, not a state-level adversary
with persistent host access.

### What you paste into the AI summary

The optional Ollama summary feature runs entirely on your machine, but
it does process the visible result snippets through a language model.
The model itself does not phone home (egress is blocked), but if you
later screenshot the summary or copy it into a different application,
those documents have left the protected boundary. That is on you.

## Reporting a vulnerability

If you believe you have found a security issue — for example, a way to
defeat the egress block, a path traversal, or a command-injection bug
in one of the integration points — **please do not open a public
issue.**

Email Liam Bindle directly at the address listed on
<https://github.com/LiamBindle> with:

- A description of the issue
- Steps to reproduce
- The version you tested against (visible in the app's status badge or
  via `GET /api/status`)

Initial acknowledgement aim: within 7 days. This is a hobby project,
not a funded one — patches are appreciated, and credit will be given
in the release notes unless you prefer otherwise.
