# Security & Threat Model

Zotero Private Search is built for researchers whose work requires a
*verifiable* guarantee that documents never leave their machine. This
document describes what the privacy guarantee actually covers, how to
verify it, and what is explicitly out of scope.

## What the privacy guarantee covers

Two containers run side by side via Docker Compose:

- **`zotero-private-search`** — the FastAPI app, Ollama, and all model
  weights. Attached *only* to the `isolated` network (`internal: true`).
- **`gateway`** — a minimal nginx:alpine reverse proxy. Attached to
  both `isolated` and `public` networks; owns the published port 8765.

Docker's bridge driver omits the default gateway for `internal: true`
networks, so the API container has no route to any external IP — on
Linux, macOS, and Windows alike. This is enforced by Docker before any
container code starts.

The egress policy in practice:

1. The API container can reach the gateway (both are on `isolated`),
   allowing nginx to forward responses back to the UI.
2. The gateway receives connections from the host on port 8765 and
   proxies them to the API container. It does not make outbound
   connections to external IPs — its nginx config only specifies
   `proxy_pass http://zotero-private-search:8765`.
3. The API container has no route to the internet and no `NET_ADMIN`
   capability. A root process inside it cannot modify network rules or
   escape the isolation.

Because this is enforced at Docker's network layer before any container
code runs, **no userspace process inside the API container can bypass
it** — not the embedding model, not Ollama, not a future Python
dependency that decides to phone home, not a malicious package that
sneaks into the supply chain after the image is built.

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

### 1. Confirm the API container has no external network

```bash
docker inspect $(docker ps --filter name=zotero-private-search -q) \
  --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}'
# Expected: only the isolated network appears — no public/bridge network
```

### 2. Confirm the isolated network has no gateway

```bash
docker network inspect \
  $(docker inspect $(docker ps --filter name=zotero-private-search -q) \
    --format '{{range $k,$v := .NetworkSettings.Networks}}{{$v.NetworkID}}{{end}}') \
  --format '{{.Internal}}'
# Expected: true
```

The UI's Network Isolation badge runs an active TCP probe to `1.1.1.1:443`
from inside the container on startup. A green ✓ means the probe timed out
(egress blocked). A red ✕ means it connected — investigate immediately.

### 3. Audit the source

The source is AGPL-3.0. The relevant files are short and self-contained:

- [`entrypoint.sh`](entrypoint.sh) — startup log; no iptables needed
- [`desktop/src/main.js`](desktop/src/main.js) — generates `docker-compose.yml` and `nginx.conf` at runtime
- [`Dockerfile`](Dockerfile) — model bake-in, telemetry env vars
- [`pyproject.toml`](pyproject.toml) — telemetry env vars for the dev
  environment
- [`main.py`](main.py) — every network-touching code path

If your IT department, ethics board, or collaborators want independent
verification, they can read these files end to end in well under an
hour.

## Out of scope

Be honest about what this tool does **not** protect against.

### Physical access

A user with physical access to the host machine can read `~/Zotero`,
read the on-disk vector index (`CHROMA_PATH`), or take a screenshot of
the running app. This tool does not encrypt your library at rest — that
is your operating system's job. Use full-disk encryption.

### Host-level shell access

A user who can `docker exec` into the container, run arbitrary commands
as root on the host, or modify the image before it starts can do
anything they want. The threat model assumes the host is a workstation
that *you* control.

### Supply-chain attacks on the base image and dependencies

Docker base images, Ollama, fastembed weights, and Python packages are
pinned to specific versions, but they are not signed end-to-end. A
sufficiently motivated attacker who compromises an upstream registry
could ship a malicious build. The Docker network isolation is
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
