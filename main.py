import json as _json
import logging
import os
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Thread

import chromadb
from chromadb.config import Settings as ChromaSettings
from fastembed import TextEmbedding
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

import ollama as _ollama
from indexer import CHROMA_COLLECTION, get_collections, get_item_ids_for_collection, get_pending_count, run_indexing

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
MODEL_CACHE    = os.environ.get("FASTEMBED_CACHE_PATH",  str(_HOME / ".cache" / "zotero-semantic-search" / "models"))
CHROMA_PATH    = os.environ.get("CHROMA_PATH",           str(_HOME / ".local" / "share" / "zotero-semantic-search" / "chroma"))
OLLAMA_URL     = os.environ.get("OLLAMA_URL",            "http://localhost:11434")
OLLAMA_MODEL   = os.environ.get("OLLAMA_MODEL",          "llama3.2")

# ── Global singletons ──────────────────────────────────────────────────────────

_model: TextEmbedding | None = None
_chroma_client = None
_chroma_col = None
_ollama_available: bool = False

_index_state: dict = {
    "running": False,
    "current": 0,
    "total": 0,
    "message": "idle",
    "last_result": None,
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _model, _chroma_client, _chroma_col, _ollama_available

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

    _ollama_available = _ollama.check_available(OLLAMA_URL)
    if _ollama_available:
        log.info("Ollama available at %s (model: %s) — HyDE and query expansion enabled.",
                 OLLAMA_URL, OLLAMA_MODEL)
    else:
        log.info("Ollama not detected at %s — standard embedding search only.", OLLAMA_URL)

    yield


app = FastAPI(lifespan=lifespan)


# ── UI ─────────────────────────────────────────────────────────────────────────

@app.get("/")
async def index():
    return FileResponse("index.html")


# ── Status ─────────────────────────────────────────────────────────────────────

@app.get("/api/status")
async def api_status():
    version = await _ollama.get_version(OLLAMA_URL) if _ollama_available else None
    return {
        "ollama": {"available": _ollama_available, "model": OLLAMA_MODEL, "version": version},
        "embed_model": EMBED_MODEL,
    }


# ── Search ─────────────────────────────────────────────────────────────────────

@app.get("/api/search")
async def search(q: str = "", collection: str = "",
                 limit: int = 25, min_score: float = 0.55):
    if not q.strip():
        return {"results": [], "search_context": None}

    # HyDE: if Ollama available, embed a hypothetical matching document instead of q
    search_context: str | None = None
    if _ollama_available:
        hyp = await _ollama.hyde_text(q, OLLAMA_MODEL, OLLAMA_URL)
        if hyp:
            search_context = hyp
            vector = next(_model.embed([hyp])).tolist()
        else:
            vector = next(_model.embed([q])).tolist()
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
    if not q.strip() or not _ollama_available:
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
        _index_state.update({
            "running": True, "current": 0, "total": 0,
            "message": "Starting...", "last_result": None,
        })

        def _progress(current, total, message):
            _index_state.update({"current": current, "total": total,
                                  "message": message})

        try:
            result = run_indexing(
                db_path=ZOTERO_DB,
                storage_dir=ZOTERO_STORAGE,
                model=_model,
                chroma_collection=_chroma_col,
                progress_cb=_progress,
                incremental=incremental,
                collection=coll,
            )
            _index_state["last_result"] = result
        except Exception as e:
            log.exception("Indexing failed")
            _index_state["last_result"] = {"error": str(e)}
        finally:
            _index_state["running"] = False

    Thread(target=_run, args=(incremental, coll), daemon=True).start()
    return {"status": "started"}


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


@app.get("/api/open")
async def open_file(path: str):
    cmd = "open" if sys.platform == "darwin" else "xdg-open"
    subprocess.Popen([cmd, path])
    return {"ok": True}


# ── AI Summary ─────────────────────────────────────────────────────────────────

class SummaryRequest(BaseModel):
    q: str
    results: list[dict]
    context_chars: int = 3200


@app.post("/api/summary")
async def api_summary(body: SummaryRequest):
    if not _ollama_available or not body.q.strip() or not body.results:
        return JSONResponse({"error": "unavailable"}, status_code=503)

    async def event_stream():
        async for token in _ollama.stream_summary(
            body.q, body.results, OLLAMA_MODEL, OLLAMA_URL, body.context_chars
        ):
            yield f"data: {_json.dumps({'token': token})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
