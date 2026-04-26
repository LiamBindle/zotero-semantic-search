import json as _json
import logging
import os
import socket
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, Thread

import chromadb
from chromadb.config import Settings as ChromaSettings
from fastembed import TextEmbedding
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

import ollama as _ollama
from indexer import (
    CHROMA_COLLECTION,
    INDEX_DB_FILENAME,
    IndexDB,
    get_collections,
    get_item_ids_for_collection,
    get_pending_count,
    run_indexing,
)

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
# chromadb 0.5.3 has a posthog version mismatch that logs harmless errors;
# telemetry is already disabled via anonymized_telemetry=False
logging.getLogger("chromadb.telemetry.product.posthog").setLevel(logging.CRITICAL)

_HOME = Path.home()
_ZOTERO_DEFAULT = _HOME / "Zotero"

ZOTERO_DB      = os.environ.get("ZOTERO_DB",            str(_ZOTERO_DEFAULT / "zotero.sqlite"))
ZOTERO_STORAGE = os.environ.get("ZOTERO_STORAGE",       str(_ZOTERO_DEFAULT / "storage"))
EMBED_MODEL    = os.environ.get("EMBED_MODEL",           "nomic-ai/nomic-embed-text-v1.5")
MODEL_CACHE    = os.environ.get("FASTEMBED_CACHE_PATH",  str(_HOME / ".cache" / "zotero-private-search" / "models"))
CHROMA_PATH    = os.environ.get("CHROMA_PATH",           str(_HOME / ".local" / "share" / "zotero-private-search" / "chroma"))
INDEX_DB_PATH  = os.environ.get("INDEX_DB_PATH",         str(Path(CHROMA_PATH) / INDEX_DB_FILENAME))
OLLAMA_URL     = os.environ.get("OLLAMA_URL",            "http://localhost:11434")
OLLAMA_MODEL   = os.environ.get("OLLAMA_MODEL",          "llama3.2")
APP_VERSION    = os.environ.get("APP_VERSION",            "dev")

# ── Global singletons ──────────────────────────────────────────────────────────

_model: TextEmbedding | None = None
_chroma_client = None
_chroma_col = None
_index_db: IndexDB | None = None
_cancel_event: Event = Event()
_airgap_state: dict = {
    "airgapped": None,
    "mode": "unknown",
    "detail": "Probe has not run yet.",
    "probed_at": None,
}

_index_state: dict = {
    "running": False,
    "current": 0,
    "total": 0,
    "message": "idle",
}


def _probe_airgap() -> dict:
    """TCP-only egress probe to a public IP. No DNS, no HTTP, no payload —
    just a SYN to 1.1.1.1:443. If the Docker internal network is enforcing
    the egress block, the SYN never gets a response and we time out
    (mode=blocked). If the probe connects, egress is not blocked (mode=breach)."""
    probed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        with socket.create_connection(("1.1.1.1", 443), timeout=3.0):
            return {
                "airgapped": False,
                "mode": "breach",
                "detail": "TCP probe to 1.1.1.1:443 succeeded — egress is NOT blocked.",
                "probed_at": probed_at,
            }
    except (socket.timeout, OSError) as e:
        return {
            "airgapped": True,
            "mode": "blocked",
            "detail": f"TCP probe to 1.1.1.1:443 blocked ({type(e).__name__}).",
            "probed_at": probed_at,
        }


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _model, _chroma_client, _chroma_col, _index_db

    log.info("Loading embedding model '%s' from %s ...", EMBED_MODEL, MODEL_CACHE)
    _model = TextEmbedding(EMBED_MODEL, cache_dir=MODEL_CACHE)
    _ = list(_model.embed(["warmup"]))
    log.info("Model loaded.")

    _chroma_client = chromadb.PersistentClient(
        path=CHROMA_PATH,
        settings=ChromaSettings(anonymized_telemetry=False),
    )
    _chroma_col = _chroma_client.get_or_create_collection(
        name=CHROMA_COLLECTION,
        metadata={"hnsw:space": "cosine"},
    )
    log.info("ChromaDB ready. Collection '%s' has %d vectors.",
             CHROMA_COLLECTION, _chroma_col.count())

    _index_db = IndexDB(INDEX_DB_PATH)
    _index_db.mark_interrupted_runs()
    log.info("Index DB ready at %s.", INDEX_DB_PATH)

    log.info("Ollama at %s (model: %s).", OLLAMA_URL, OLLAMA_MODEL)

    global _airgap_state
    _airgap_state = _probe_airgap()
    log.info("[security] Airgap probe: %s — %s", _airgap_state["mode"], _airgap_state["detail"])

    yield


app = FastAPI(lifespan=lifespan)


# ── UI ─────────────────────────────────────────────────────────────────────────

@app.get("/")
async def index():
    return FileResponse("index.html")


# ── Status ─────────────────────────────────────────────────────────────────────

@app.get("/api/status")
async def api_status():
    version = await _ollama.get_version(OLLAMA_URL)
    return {
        "ollama": {"model": OLLAMA_MODEL, "version": version},
        "embed_model": EMBED_MODEL,
        "app_version": APP_VERSION,
    }


@app.get("/api/airgap")
async def api_airgap(recheck: bool = False):
    global _airgap_state
    if recheck or _airgap_state.get("probed_at") is None:
        _airgap_state = _probe_airgap()
    return _airgap_state


# ── Search ─────────────────────────────────────────────────────────────────────

@app.get("/api/search")
async def search(q: str = "", collection: str = "",
                 limit: int = 25, min_score: float = 0.55):
    if not q.strip():
        return {"results": [], "search_context": None}

    # HyDE: embed a hypothetical matching document instead of the raw query
    search_context: str | None = None
    hyp = await _ollama.hyde_text(q, OLLAMA_MODEL, OLLAMA_URL)
    if hyp:
        search_context = hyp
        vector = next(_model.embed([hyp])).tolist()
    else:
        vector = next(_model.embed([q])).tolist()

    raw = _chroma_col.query(
        query_embeddings=[vector],
        n_results=min(100, max(limit * 8, 40)),
        include=["metadatas", "distances"],
    )

    hits = []
    seen: set[str] = set()

    for meta, dist in zip(raw["metadatas"][0], raw["distances"][0]):
        score = round(1.0 - (dist / 2.0), 4)
        if score < min_score:
            continue
        item_id = meta["item_id"]
        if item_id in seen:
            continue
        if collection:
            coll_set = {c.strip() for c in meta.get("collection_names", "").split(";")}
            if collection not in coll_set:
                continue
        seen.add(item_id)
        hits.append({
            "score": score,
            "title": meta["title"],
            "authors": meta["authors"],
            "year": meta["year"],
            "location": meta["location"],
            "text": meta["text"],
            "collections": meta["collection_names"],
            "attach_path": meta.get("attach_path", ""),
        })
        if len(hits) >= limit:
            break

    return {"results": hits, "search_context": search_context}


# ── Query expansion ────────────────────────────────────────────────────────────

@app.get("/api/expand")
async def api_expand(q: str = ""):
    if not q.strip():
        return {"expanded": None}
    expanded = await _ollama.expand_query(q, OLLAMA_MODEL, OLLAMA_URL)
    return {"expanded": expanded}


# ── Collections ────────────────────────────────────────────────────────────────

@app.get("/api/collections")
async def api_collections():
    return {"collections": get_collections(ZOTERO_DB)}


# ── Indexing ───────────────────────────────────────────────────────────────────

@app.get("/api/index/pending")
async def index_pending(collection: str = ""):
    return get_pending_count(ZOTERO_DB, ZOTERO_STORAGE, _chroma_col, collection or None)


@app.post("/api/index")
async def start_index(incremental: bool = True, collection: str = ""):
    if _index_state["running"]:
        return JSONResponse({"error": "already running"}, status_code=409)

    coll = collection or None

    def _run(incremental: bool, coll: str | None):
        _cancel_event.clear()
        _index_state.update({
            "running": True, "current": 0, "total": 0,
            "message": "Starting...",
        })

        def _progress(current, total, message):
            _index_state.update({"current": current, "total": total,
                                  "message": message})

        try:
            run_indexing(
                db_path=ZOTERO_DB,
                storage_dir=ZOTERO_STORAGE,
                model=_model,
                chroma_collection=_chroma_col,
                progress_cb=_progress,
                incremental=incremental,
                collection=coll,
                index_db=_index_db,
                cancel_event=_cancel_event,
            )
        except Exception:
            log.exception("Indexing failed")
        finally:
            _index_state["running"] = False

    Thread(target=_run, args=(incremental, coll), daemon=True).start()
    return {"status": "started"}


@app.post("/api/index/cancel")
async def cancel_index():
    if not _index_state["running"]:
        return JSONResponse({"error": "not running"}, status_code=409)
    _cancel_event.set()
    return {"ok": True}


@app.delete("/api/index")
async def delete_index(collection: str = ""):
    global _chroma_col
    if _index_state["running"]:
        return JSONResponse({"error": "indexing in progress"}, status_code=409)
    if collection:
        item_ids = get_item_ids_for_collection(ZOTERO_DB, collection)
        if item_ids:
            results = _chroma_col.get(where={"item_id": {"$in": item_ids}}, include=[])
            if results["ids"]:
                _chroma_col.delete(ids=results["ids"])
        log.info("Cleared index for collection '%s' (%d items).", collection, len(item_ids))
    else:
        _chroma_client.delete_collection(CHROMA_COLLECTION)
        _chroma_col = _chroma_client.get_or_create_collection(
            name=CHROMA_COLLECTION,
            metadata={"hnsw:space": "cosine"},
        )
        log.info("Index cleared.")
    return {"ok": True}


@app.get("/api/index/status")
async def index_status():
    return dict(_index_state)


@app.get("/api/index/summary")
async def index_summary():
    if not _index_db:
        return JSONResponse({"error": "no summary yet"}, status_code=404)
    result = _index_db.get_summary()
    if not result:
        return JSONResponse({"error": "no summary yet"}, status_code=404)
    return result


# ── AI Summary ─────────────────────────────────────────────────────────────────

class SummaryRequest(BaseModel):
    q: str
    results: list[dict]
    context_chars: int = 3200


@app.post("/api/summary")
async def api_summary(body: SummaryRequest):
    if not body.q.strip() or not body.results:
        return JSONResponse({"error": "unavailable"}, status_code=503)

    async def event_stream():
        async for token in _ollama.stream_summary(
            body.q, body.results, OLLAMA_MODEL, OLLAMA_URL, body.context_chars
        ):
            yield f"data: {_json.dumps({'token': token})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
