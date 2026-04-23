import logging
import os
from contextlib import asynccontextmanager
from threading import Thread

import chromadb
from chromadb.config import Settings as ChromaSettings
from fastembed import TextEmbedding
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from indexer import CHROMA_COLLECTION, get_collections, run_indexing

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
# chromadb 0.5.3 has a posthog version mismatch that logs harmless errors;
# telemetry is already disabled via anonymized_telemetry=False
logging.getLogger("chromadb.telemetry.product.posthog").setLevel(logging.CRITICAL)

ZOTERO_DB = os.environ.get("ZOTERO_DB", "/zotero/zotero.sqlite")
ZOTERO_STORAGE = os.environ.get("ZOTERO_STORAGE", "/zotero/storage")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "BAAI/bge-small-en-v1.5")
MODEL_CACHE = os.environ.get("FASTEMBED_CACHE_PATH", "/model_cache")
CHROMA_PATH = os.environ.get("CHROMA_PATH", "/chroma_data")

# ── Global singletons ──────────────────────────────────────────────────────────

_model: TextEmbedding | None = None
_chroma_col = None

_index_state: dict = {
    "running": False,
    "current": 0,
    "total": 0,
    "message": "idle",
    "last_result": None,
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _model, _chroma_col

    log.info("Loading embedding model '%s' from %s ...", EMBED_MODEL, MODEL_CACHE)
    _model = TextEmbedding(EMBED_MODEL, cache_dir=MODEL_CACHE)
    # Warm up: run one embedding to initialise ONNX runtime buffers now,
    # so startup memory is representative and there are no surprises later.
    _ = list(_model.embed(["warmup"]))
    log.info("Model loaded.")

    client = chromadb.PersistentClient(
        path=CHROMA_PATH,
        settings=ChromaSettings(anonymized_telemetry=False),
    )
    _chroma_col = client.get_or_create_collection(
        name=CHROMA_COLLECTION,
        metadata={"hnsw:space": "cosine"},
    )
    log.info("ChromaDB ready. Collection '%s' has %d vectors.",
             CHROMA_COLLECTION, _chroma_col.count())

    yield


app = FastAPI(lifespan=lifespan)
templates = Jinja2Templates(directory="templates")


# ── UI ─────────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    needs_indexing = _chroma_col.count() == 0
    collections = [] if needs_indexing else get_collections(_chroma_col)
    return templates.TemplateResponse(request, "index.html", {
        "needs_indexing": needs_indexing,
        "collections": collections,
        "embed_model": EMBED_MODEL,
    })


# ── Search ─────────────────────────────────────────────────────────────────────

@app.get("/api/search")
async def search(q: str = "", collection: str = "", limit: int = 10):
    if not q.strip():
        return {"results": []}

    # fastembed returns a generator; take the first (and only) embedding
    vector = next(_model.embed([q])).tolist()

    raw = _chroma_col.query(
        query_embeddings=[vector],
        n_results=min(50, max(limit * 5, 20)),
        include=["metadatas", "distances"],
    )

    hits = []
    seen: set[str] = set()

    for meta, dist in zip(raw["metadatas"][0], raw["distances"][0]):
        item_id = meta["item_id"]
        if item_id in seen:
            continue
        if collection:
            coll_set = {c.strip() for c in meta.get("collection_names", "").split(";")}
            if collection not in coll_set:
                continue
        seen.add(item_id)
        # ChromaDB cosine distance: 0 = identical, 2 = opposite → convert to similarity
        score = round(1.0 - (dist / 2.0), 4)
        hits.append({
            "score": score,
            "title": meta["title"],
            "authors": meta["authors"],
            "year": meta["year"],
            "location": meta["location"],
            "text": meta["text"],
            "collections": meta["collection_names"],
        })
        if len(hits) >= limit:
            break

    return {"results": hits}


# ── Collections ────────────────────────────────────────────────────────────────

@app.get("/api/collections")
async def api_collections():
    return {"collections": get_collections(_chroma_col)}


# ── Indexing ───────────────────────────────────────────────────────────────────

@app.post("/api/index")
async def start_index():
    if _index_state["running"]:
        return JSONResponse({"error": "already running"}, status_code=409)

    def _run():
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
            )
            _index_state["last_result"] = result
        except Exception as e:
            log.exception("Indexing failed")
            _index_state["last_result"] = {"error": str(e)}
        finally:
            _index_state["running"] = False

    Thread(target=_run, daemon=True).start()
    return {"status": "started"}


@app.get("/api/index/status")
async def index_status():
    return dict(_index_state)
