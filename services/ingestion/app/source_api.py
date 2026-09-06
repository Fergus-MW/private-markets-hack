"""Authenticated connector handoff: retained bytes -> canonical graph."""
import json
from datetime import date
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Form, HTTPException, UploadFile
from pydantic import Field

from app.connectors import Item, MAX_BYTES
from app.extraction import Ingestion
from app.graph import Strict
from app.graph_api import load, save

router = APIRouter()


class Envelope(Strict):
    provider: Literal["gmail", "drive", "fixture"]
    account: str = Field(min_length=1, max_length=256)
    external_id: str = Field(min_length=1, max_length=512)
    revision: str = ""
    metadata: dict = Field(default_factory=dict)
    fund_id: str | None = None
    snapshot_as_of: date | None = None
    use_gemini: bool = False


@router.post("/sources")
def ingest_source(file: UploadFile, envelope: str = Form(...)):
    try:
        request = Envelope.model_validate_json(envelope)
    except ValueError:
        raise HTTPException(422, "Invalid connector envelope") from None
    content = file.file.read(MAX_BYTES + 1)
    if len(content) > MAX_BYTES:
        raise HTTPException(413, "Maximum source size is 20 MiB")
    if not content:
        raise HTTPException(422, "Source is empty")
    store, graph = load()
    filename = Path((file.filename or "source").replace("\\", "/")).name
    from app.identity import tenant
    item = Item(request.provider, tenant() or request.account, request.external_id, filename, content,
                request.revision, "email" if filename.lower().endswith(".eml") else "file", request.metadata)
    try:
        source_id = Ingestion(graph, store, use_gemini=request.use_gemini, fund_id=request.fund_id,
            snapshot_as_of=request.snapshot_as_of.isoformat() if request.snapshot_as_of else None).ingest(item)
    except (ValueError, OverflowError, KeyError) as error:
        raise HTTPException(422, str(error)) from None
    save(store, graph)
    return {"source_id": source_id, "document_id": graph.state.sources[source_id].document_id,
            "revision": graph.state.revision, "warnings": graph.state.sources[source_id].warnings}
