import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, UploadFile
from app.context import PIPELINE_VERSION, context_page, prepare_context
from app.parser import SUPPORTED, parse_document
from app.store import Store

logger = logging.getLogger(__name__)
MAX_BYTES = 20 * 1024 * 1024
app = FastAPI(title="Document context ingestion", version="2.0.0")


@app.get("/healthz")
def health():
    return {"status": "ok"}


@app.get("/readyz")
def ready():
    try:
        Store().query("RETURN true;")
    except Exception:
        raise HTTPException(503, "Database unavailable") from None
    return {"status": "ready"}


@app.get("/formats")
def formats():
    return {"extensions": sorted(SUPPORTED), "max_bytes": MAX_BYTES,
            "pdf_strategies": ["auto", "ocr_only", "hi_res"], "ocr_languages": ["eng"]}


def load_document(document_id):
    if len(document_id) != 64 or any(c not in "0123456789abcdef" for c in document_id):
        raise HTTPException(422, "Expected a SHA-256 document ID")
    try:
        document = Store().get(document_id)
    except Exception:
        logger.exception("Document retrieval failed")
        raise HTTPException(503, "Database unavailable") from None
    if not document:
        raise HTTPException(404, "Document not found")
    if "chunks" not in document:
        raise HTTPException(409, "Legacy document: reupload the source to prepare agent context")
    return document


@app.get("/documents/{document_id}")
def document_manifest(document_id: str):
    doc = load_document(document_id)
    return {key: doc[key] for key in ("key", "filename", "sha256", "size_bytes", "status",
            "pipeline_version", "processed_at", "element_count", "chunk_count", "warnings")}


@app.get("/documents/{document_id}/context")
def document_context(document_id: str, offset: int = Query(0, ge=0),
                     limit: int = Query(10, ge=1, le=50),
                     max_characters: int = Query(20000, ge=4000, le=100000)):
    return context_page(load_document(document_id), offset, limit, max_characters)


@app.get("/documents/{document_id}/elements")
def document_elements(document_id: str, offset: int = Query(0, ge=0),
                      limit: int = Query(20, ge=1, le=100)):
    doc = load_document(document_id)
    elements = doc["elements"][offset:offset + limit]
    end = offset + len(elements)
    return {"document_id": document_id, "elements": elements,
            "next_offset": end if end < len(doc["elements"]) else None}


@app.post("/documents")
def ingest(file: UploadFile, pdf_strategy: Literal["auto", "ocr_only", "hi_res"] = "auto"):
    from unstructured.chunking.title import chunk_by_title

    filename = Path((file.filename or "document").replace("\\", "/")).name
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED:
        raise HTTPException(415, "Unsupported format; GET /formats lists supported extensions")
    with TemporaryDirectory() as directory:
        path = Path(directory) / ("input" + suffix)
        sha, size = hashlib.sha256(), 0
        with path.open("wb") as output:
            while chunk := file.file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise HTTPException(413, "Maximum document size is 20 MiB")
                sha.update(chunk)
                output.write(chunk)
        if not size:
            raise HTTPException(422, "Document is empty")
        try:
            parsed, warnings = parse_document(path, pdf_strategy)
            elements, chunks = prepare_context(parsed, sha.hexdigest(), filename, chunk_by_title)
        except OverflowError as error:
            raise HTTPException(413, str(error)) from None
        except Exception:
            logger.exception("Document parsing or chunking failed")
            raise HTTPException(422, "Document could not be parsed; check format, encryption, and document size") from None
    key = sha.hexdigest()
    document = {"key": key, "sha256": key, "filename": filename, "size_bytes": size,
                "elements": elements, "chunks": chunks, "element_count": len(elements),
                "chunk_count": len(chunks), "pipeline_version": PIPELINE_VERSION,
                "pdf_strategy": pdf_strategy, "warnings": warnings,
                "processed_at": datetime.now(timezone.utc).isoformat(), "status": "complete"}
    try:
        Store().save(document)
    except Exception:
        logger.exception("Document persistence failed")
        raise HTTPException(503, "Persistence failed; retry the same document safely") from None
    return {"document_id": key, "status": "complete", "elements": len(elements),
            "chunks": len(chunks), "pipeline_version": PIPELINE_VERSION,
            "context_url": f"/documents/{key}/context", "warnings": warnings}
