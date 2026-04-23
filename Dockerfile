FROM debian:bookworm-slim

# System dependencies
# libgomp1 is required by onnxruntime (used by fastembed) and other ML libs
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates libgomp1 zstd iptables \
    && rm -rf /var/lib/apt/lists/*

# Install Ollama
RUN curl -fsSL https://ollama.com/install.sh | sh

# Install pixi
RUN curl -fsSL https://pixi.sh/install.sh | bash
ENV PATH="/root/.pixi/bin:$PATH"

# Pre-pull the Ollama LLM (only needs Ollama — no app files required)
# Override at build time with --build-arg OLLAMA_MODEL=...
ARG OLLAMA_MODEL=llama3.2
ENV OLLAMA_MODEL=${OLLAMA_MODEL}
RUN ollama serve & \
    OLLAMA_PID=$! && \
    echo "Waiting for Ollama..." && \
    timeout 60 bash -c \
        'until curl -sf http://localhost:11434/api/tags > /dev/null 2>&1; do sleep 2; done' && \
    echo "Pulling ${OLLAMA_MODEL}..." && \
    ollama pull ${OLLAMA_MODEL} && \
    kill $OLLAMA_PID && \
    echo "Ollama model ready."

WORKDIR /app

# Python dependencies — copied before app code so this layer is cached independently
COPY pyproject.toml pixi.lock ./
RUN pixi install
ENV LD_LIBRARY_PATH=/app/.pixi/envs/default/lib

# Pre-download the fastembed embedding model (needs pixi env, but not app code)
# Override at build time with --build-arg EMBED_MODEL=...
ARG EMBED_MODEL=nomic-ai/nomic-embed-text-v1.5
ENV FASTEMBED_CACHE_PATH=/app/models
ENV EMBED_MODEL=${EMBED_MODEL}
RUN /app/.pixi/envs/default/bin/python -c "\
import os; \
from fastembed import TextEmbedding; \
m = TextEmbedding(os.environ['EMBED_MODEL'], cache_dir=os.environ['FASTEMBED_CACHE_PATH']); \
list(m.embed(['warmup'])); \
print('Embedding model ready.')"

# App code (copied last — changes here don't invalidate any layer above)
COPY . .

ENV ZOTERO_DB=/zotero/zotero.sqlite
ENV ZOTERO_STORAGE=/zotero/storage
ENV CHROMA_PATH=/data/chroma
ENV OLLAMA_URL=http://localhost:11434

EXPOSE 8000

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
