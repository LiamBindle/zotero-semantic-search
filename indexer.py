import gc
import hashlib
import json
import logging
import os
import shutil
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastembed import TextEmbedding

from extractors import extract

log = logging.getLogger(__name__)

CHROMA_COLLECTION = "zotero_docs"
EMBED_BATCH = 8  # small batches keep peak tensor memory low
REPORT_FILENAME = "indexing-report.json"

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

def _candidate_items(
    db_path: str, storage_dir: str, collection: str | None = None
) -> tuple[list[tuple], list[tuple]]:
    """Return ``(resolvable, missing)`` for items in the (optionally filtered)
    collection.

    - ``resolvable`` is a list of tuples for attachments that exist on disk:
      ``(item_id, attach_key, attach_path, row, creators_by_item, coll_by_item)``
    - ``missing`` lists items whose attachment metadata is in Zotero but
      whose file is absent on disk: ``(item_id, attach_key, raw_path, row,
      creators_by_item, coll_by_item)``
    """
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

    resolvable: list[tuple] = []
    missing: list[tuple] = []
    for row in items:
        if collection and collection not in coll_by_item.get(row["itemID"], []):
            continue
        attach_path = _resolve_path(row["attach_path"], storage_dir, row["attach_key"])
        if attach_path:
            resolvable.append((row["itemID"], row["attach_key"], attach_path,
                               row, creators_by_item, coll_by_item))
        else:
            missing.append((row["itemID"], row["attach_key"], row["attach_path"] or "",
                            row, creators_by_item, coll_by_item))
    return resolvable, missing


def _resolvable_items(
    db_path: str, storage_dir: str, collection: str | None = None
) -> list[tuple]:
    """Back-compat wrapper for callers that only need the on-disk subset."""
    resolvable, _ = _candidate_items(db_path, storage_dir, collection)
    return resolvable


def get_pending_count(
    db_path: str, storage_dir: str, chroma_collection, collection: str | None = None
) -> dict:
    """Return {"pending": N, "total": N} — attachments not yet in the index."""
    candidates = _resolvable_items(db_path, storage_dir, collection)
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
    collection: str | None = None,
    report_path: str | None = None,
) -> dict:
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    started_perf = time.perf_counter()
    resolvable, missing = _candidate_items(db_path, storage_dir, collection)

    if incremental and resolvable:
        first_chunk_ids = [_make_id(item_id, attach_key, 0)
                           for item_id, attach_key, *_ in resolvable]
        existing = set(chroma_collection.get(ids=first_chunk_ids)["ids"])
        candidates = [c for c, cid in zip(resolvable, first_chunk_ids)
                      if cid not in existing]
    else:
        candidates = list(resolvable)

    total = len(candidates)
    vectors_stored = 0
    items_report: list[dict] = []
    counts = {
        "indexed": 0,
        "skipped_unsupported": 0,
        "skipped_empty": 0,
        "extraction_failed": 0,
        "no_attachment_on_disk": len(missing),
    }

    # Files Zotero references but cannot find on disk are reported even
    # though we never opened them — they would otherwise vanish silently.
    for item_id, attach_key, raw_path, row, creators_by_item, coll_by_item in missing:
        items_report.append({
            "item_id": str(item_id),
            "title": row["title"] or "Untitled",
            "authors": "; ".join(creators_by_item.get(item_id, [])),
            "year": (row["year"] or "")[:4],
            "collections": "; ".join(coll_by_item.get(item_id, [])),
            "attach_path": raw_path,
            "status": "no_attachment_on_disk",
            "chunks": 0,
            "error": None,
        })

    for idx, (item_id, attach_key, attach_path, row, creators_by_item, coll_by_item) in enumerate(candidates):
        title = row["title"] or "Untitled"
        year = (row["year"] or "")[:4]
        authors = "; ".join(creators_by_item.get(item_id, []))
        coll_str = "; ".join(coll_by_item.get(item_id, []))

        if progress_cb:
            progress_cb(idx, total, f"Indexing: {title}")

        chunks, status, error = extract(attach_path)

        item_entry = {
            "item_id": str(item_id),
            "title": title,
            "authors": authors,
            "year": year,
            "collections": coll_str,
            "attach_path": attach_path,
            "status": "indexed",
            "chunks": 0,
            "error": error,
        }

        if status == "unsupported":
            item_entry["status"] = "skipped_unsupported"
            counts["skipped_unsupported"] += 1
            items_report.append(item_entry)
            continue
        if status == "empty":
            item_entry["status"] = "skipped_empty"
            counts["skipped_empty"] += 1
            items_report.append(item_entry)
            log.debug("No chunks extracted from %s", attach_path)
            continue
        if status == "failed":
            item_entry["status"] = "extraction_failed"
            counts["extraction_failed"] += 1
            items_report.append(item_entry)
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

        item_entry["chunks"] = len(chunks)
        counts["indexed"] += 1
        items_report.append(item_entry)

        del chunks, texts, vectors, ids_buf, embs_buf, metas_buf
        gc.collect()

    if progress_cb:
        progress_cb(total, total, "Done")

    finished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    duration_s = round(time.perf_counter() - started_perf, 2)

    report = {
        "status": "done",
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_s": duration_s,
        "collection": collection,
        "incremental": incremental,
        "items_processed": total,
        "vectors_stored": vectors_stored,
        "counts": counts,
        "items": items_report,
    }

    if report_path:
        try:
            Path(report_path).parent.mkdir(parents=True, exist_ok=True)
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2)
        except OSError as e:
            log.warning("Could not persist indexing report to %s: %s", report_path, e)

    return report


# ── Collection index helpers ───────────────────────────────────────────────────

def get_item_ids_for_collection(db_path: str, collection: str) -> list[str]:
    """Return string item IDs for all items in the given collection."""
    conn, tmp_db = _open_db(db_path)
    try:
        rows = conn.execute("""
            SELECT DISTINCT ci.itemID
            FROM collectionItems ci
            JOIN collections col ON col.collectionID = ci.collectionID
            WHERE col.collectionName = ?
        """, (collection,)).fetchall()
        return [str(row["itemID"]) for row in rows]
    finally:
        conn.close()
        os.unlink(tmp_db)


# ── Collection list helper ─────────────────────────────────────────────────────

def get_collections(db_path: str) -> list[str]:
    try:
        conn, tmp = _open_db(db_path)
        try:
            rows = conn.execute(
                "SELECT DISTINCT collectionName FROM collections ORDER BY collectionName"
            ).fetchall()
            return [r[0] for r in rows]
        finally:
            conn.close()
            try:
                os.unlink(tmp)
            except OSError:
                pass
    except Exception as e:
        log.warning("get_collections failed: %s", e)
        return []
