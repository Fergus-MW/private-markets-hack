import hashlib
import logging
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import FastAPI, HTTPException, UploadFile
from app.entities import PIPELINE_VERSION, extract
from app.store import Store

logger = logging.getLogger(__name__)
MAX_BYTES = 20 * 1024 * 1024
MAX_TEXT = 1_000_000
SUPPORTED = {".pdf", ".docx", ".txt", ".html", ".htm", ".md"}


@asynccontextmanager
async def lifespan(app):
    import spacy
    app.state.nlp = spacy.load("en_core_web_sm")
    yield


app = FastAPI(title="Document ingestion", lifespan=lifespan)


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


@app.post("/documents")
def ingest(file: UploadFile):
    from unstructured.partition.auto import partition

    filename = Path(file.filename or "document").name
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED:
        raise HTTPException(415, "Supported formats: PDF, DOCX, TXT, HTML, Markdown")
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
        if suffix == ".pdf":
            with path.open("rb") as source:
                if b"%PDF-" not in source.read(1024):
                    raise HTTPException(422, "Invalid PDF header")
        if suffix == ".docx" and not zipfile.is_zipfile(path):
            raise HTTPException(422, "Invalid DOCX container")
        try:
            parsed = partition(filename=str(path), strategy="fast", languages=["eng"])
            if suffix == ".pdf" and not any(str(e.text).strip() for e in parsed):
                parsed = partition(filename=str(path), strategy="ocr_only", languages=["eng"])
            elements = [{"text": str(e.text), "category": e.category,
                         "page_number": e.metadata.page_number} for e in parsed if str(e.text).strip()]
        except Exception:
            logger.exception("Document parsing failed")
            raise HTTPException(422, "Document could not be parsed") from None
    if not elements:
        raise HTTPException(422, "Document contains no extractable text")
    if sum(len(e["text"]) for e in elements) > MAX_TEXT:
        raise HTTPException(413, "Maximum extracted text is 1 million characters")
    key = sha.hexdigest()
    entities, mentions = extract(elements, app.state.nlp, key)
    document = {"key": key, "sha256": key, "filename": filename, "size_bytes": size,
                "elements": elements, "pipeline_version": PIPELINE_VERSION,
                "processed_at": datetime.now(timezone.utc).isoformat(), "status": "complete"}
    try:
        Store().save(document, entities, mentions)
    except Exception:
        logger.exception("Document persistence failed")
        raise HTTPException(503, "Persistence failed; retry the same document safely") from None
    return {"document_id": key, "status": "complete", "elements": len(elements),
            "people": sum(e["kind"] == "person" for e in entities),
            "companies": sum(e["kind"] == "company" for e in entities),
            "mentions": len(mentions), "pipeline_version": PIPELINE_VERSION}
