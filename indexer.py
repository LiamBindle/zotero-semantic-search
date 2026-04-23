import gc
import hashlib
import logging
import os
import shutil
import sqlite3
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastembed import TextEmbedding

from extractors import extract

log = logging.getLogger(__name__)

CHROMA_COLLECTION = "zotero_docs"
EMBED_BATCH = 8  # small batches keep peak tensor memory low

# ── SQLite queries ─────────────────────────────────────────────────────────────

_ITEMS_SQL = """
SELECT
    i.itemID,
    (SELECT idv.value
     FROM itemData id2
     JOIN fields f2 ON f2.fieldID = id2.fieldID AND f2.fieldName = 'title'
     JOIN itemDataValues idv ON idv.valueID = id2.valueID
     WHERE id2.itemID = i.itemID
     LIMIT 1) AS title,
    (SELECT idv.value
     FROM itemData id2
     JOIN fields f2 ON f2.fieldID = id2.fieldID AND f2.fieldName = 'date'
     JOIN itemDataValues idv ON idv.valueID = id2.valueID
     WHERE id2.itemID = i.itemID
     LIMIT 1) AS year,
    ia.path          AS attach_path,
    i_att.key        AS attach_key
FROM items i
JOIN itemTypes it         ON it.itemTypeID   = i.itemTypeID
JOIN itemAttachments ia   ON ia.parentItemID = i.itemID
JOIN items i_att          ON i_att.itemID    = ia.itemID
WHERE it.typeName != 'attachment'
  AND ia.path IS NOT NULL
  AND i.itemID     NOT IN (SELECT itemID FROM deletedItems)
  AND i_att.itemID NOT IN (SELECT itemID FROM deletedItems)
"""

_CREATORS_SQL = """
SELECT ic.itemID, c.lastName, c.firstName
FROM itemCreators ic
JOIN creators c ON c.creatorID = ic.creatorID
ORDER BY ic.itemID, ic.orderIndex
"""

_COLLECTIONS_SQL = """
SELECT ci.itemID, col.collectionName
FROM collectionItems ci
JOIN collections col ON col.collectionID = ci.collectionID
"""

# ── Database helpers ───────────────────────────────────────────────────────────

def _open_db(db_path: str):
    tmp = f"/tmp/zotero_ro_{os.getpid()}.sqlite"
    shutil.copy2(db_path, tmp)
    conn = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn, tmp


# ── Path resolution ────────────────────────────────────────────────────────────

def _resolve_path(zotero_path: str, storage_dir: str, attach_key: str = "") -> str | None:
    if not zotero_path:
        return None
    if zotero_path.startswith("storage:"):
        filename = zotero_path[len("storage:"):]
        if attach_key:
            p = Path(storage_dir) / attach_key / filename
            if p.exists():
                return str(p)
        p = Path(storage_dir) / filename
        return str(p) if p.exists() else None
    p = Path(zotero_path)
    return str(p) if p.exists() else None


# ── Deterministic chunk IDs ────────────────────────────────────────────────────

def _make_id(item_id: int, attach_key: str, chunk_idx: int) -> str:
    raw = f"{item_id}:{attach_key}:{chunk_idx}".encode()
    return str(uuid.UUID(hashlib.sha256(raw).hexdigest()[:32]))


# ── Main indexing entry point ──────────────────────────────────────────────────

def _resolvable_items(db_path: str, storage_dir: str) -> list[tuple]:
    """Return [(item_id, attach_key, attach_path, row), ...] for all attachments that exist on disk."""
    conn, tmp_db = _open_db(db_path)
    try:
        items = conn.execute(_ITEMS_SQL).fetchall()
        creators_rows = conn.execute(_CREATORS_SQL).fetchall()
        coll_rows = conn.execute(_COLLECTIONS_SQL).fetchall()
    finally:
        conn.close()
        os.unlink(tmp_db)

    creators_by_item: dict[int, list[str]] = {}
    for r in creators_rows:
        creators_by_item.setdefault(r["itemID"], []).append(
            f"{r['lastName']}, {r['firstName']}"
        )

    coll_by_item: dict[int, list[str]] = {}
    for r in coll_rows:
        coll_by_item.setdefault(r["itemID"], []).append(r["collectionName"])

    result = []
    for row in items:
        attach_path = _resolve_path(row["attach_path"], storage_dir, row["attach_key"])
        if attach_path:
            result.append((row["itemID"], row["attach_key"], attach_path,
                           row, creators_by_item, coll_by_item))
    return result


def get_pending_count(db_path: str, storage_dir: str, chroma_collection) -> dict:
    """Return {"pending": N, "total": N} — attachments not yet in the index."""
    candidates = _resolvable_items(db_path, storage_dir)
    if not candidates:
        return {"pending": 0, "total": 0}
    first_chunk_ids = [_make_id(item_id, attach_key, 0)
                       for item_id, attach_key, *_ in candidates]
    existing = set(chroma_collection.get(ids=first_chunk_ids)["ids"])
    pending = sum(1 for cid in first_chunk_ids if cid not in existing)
    return {"pending": pending, "total": len(candidates)}


def run_indexing(
    db_path: str,
    storage_dir: str,
    model: "TextEmbedding",
    chroma_collection,
    progress_cb=None,
    incremental: bool = True,
) -> dict:
    candidates = _resolvable_items(db_path, storage_dir)

    if incremental and candidates:
        first_chunk_ids = [_make_id(item_id, attach_key, 0)
                           for item_id, attach_key, *_ in candidates]
        existing = set(chroma_collection.get(ids=first_chunk_ids)["ids"])
        candidates = [c for c, cid in zip(candidates, first_chunk_ids)
                      if cid not in existing]

    total = len(candidates)
    vectors_stored = 0

    for idx, (item_id, attach_key, attach_path, row, creators_by_item, coll_by_item) in enumerate(candidates):
        title = row["title"] or "Untitled"
        year = (row["year"] or "")[:4]
        authors = "; ".join(creators_by_item.get(item_id, []))
        coll_str = "; ".join(coll_by_item.get(item_id, []))

        if progress_cb:
            progress_cb(idx, total, f"Indexing: {title}")

        chunks = extract(attach_path)
        if not chunks:
            log.debug("No chunks extracted from %s", attach_path)
            continue

        log.info("item %d (%s): %d chunks", item_id, Path(attach_path).name, len(chunks))

        texts = [c["text"] for c in chunks]
        vectors = [v.tolist() for v in model.embed(texts, batch_size=EMBED_BATCH)]

        ids_buf, embs_buf, metas_buf = [], [], []
        for chunk_idx, (chunk, vec) in enumerate(zip(chunks, vectors)):
            ids_buf.append(_make_id(item_id, attach_key, chunk_idx))
            embs_buf.append(vec)
            metas_buf.append({
                "item_id": str(item_id),
                "title": title,
                "authors": authors,
                "year": year,
                "collection_names": coll_str,
                "location": chunk["location"],
                "text": chunk["text"],
                "attach_key": attach_key,
                "attach_path": attach_path,
            })
            vectors_stored += 1

        chroma_collection.upsert(ids=ids_buf, embeddings=embs_buf, metadatas=metas_buf)

        del chunks, texts, vectors, ids_buf, embs_buf, metas_buf
        gc.collect()

    if progress_cb:
        progress_cb(total, total, "Done")

    return {
        "status": "done",
        "items_processed": total,
        "vectors_stored": vectors_stored,
    }


# ── Collection list helper ─────────────────────────────────────────────────────

def get_collections(chroma_collection) -> list[str]:
    try:
        count = chroma_collection.count()
        if count == 0:
            return []
        seen: set[str] = set()
        offset = 0
        while offset < count:
            result = chroma_collection.get(
                limit=1000, offset=offset, include=["metadatas"]
            )
            for meta in result["metadatas"]:
                for name in meta.get("collection_names", "").split(";"):
                    name = name.strip()
                    if name:
                        seen.add(name)
            offset += 1000
        return sorted(seen)
    except Exception as e:
        log.warning("get_collections failed: %s", e)
        return []
