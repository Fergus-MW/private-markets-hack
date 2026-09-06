"""Graph and connector API, protected by the service's existing IAM boundary."""
import logging
from datetime import date
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import Field

from app.connectors import GoogleConnector
from app.extraction import Ingestion
from app.graph import ENTITIES, Entity, GraphState, Strict
from app.store import GraphStore

logger = logging.getLogger(__name__)
router = APIRouter()


class ProjectScope(Strict):
    fund_id: str
    management_company_id: str
    quarter: str = Field(pattern=r"^\d{4}-Q[1-4]$")
    workflow_type: str = Field(min_length=1)


class SyncRequest(Strict):
    provider: Literal["gmail", "drive"]
    query: str = Field(min_length=1, max_length=2000)
    page_token: str | None = None
    page_size: int = Field(default=10, ge=1, le=20)
    use_gemini: bool = False
    fund_id: str | None = None
    project_scope: ProjectScope | None = None
    snapshot_as_of: date | None = None


class EntityRequest(Strict):
    entity: Entity
    source_id: str


class MergeRequest(Strict):
    keep: str
    duplicate: str
    evidence_source_id: str


def load():
    store = GraphStore()
    try:
        return store, store.load_graph()
    except Exception:
        logger.exception("Graph retrieval failed")
        raise HTTPException(503, "Graph unavailable") from None


def save(store, graph):
    try:
        store.save_graph(graph)
    except Exception:
        logger.exception("Graph commit failed")
        raise HTTPException(503, "Graph commit failed or changed concurrently; retry the request") from None


@router.get("/graph/schema")
def schema():
    return GraphState.model_json_schema()


@router.post("/connectors/sync")
def sync(request: SyncRequest):
    from app.identity import tenant
    if tenant():
        raise HTTPException(409, 'Use the account-scoped connector worker for synchronization')
    store, graph = load()
    connector = GoogleConnector()
    try:
        items, next_token = connector.page(request.provider, request.query, request.page_token, request.page_size)
        fund_id = request.project_scope.fund_id if request.project_scope else request.fund_id
        if request.fund_id and request.project_scope and graph.resolve(request.fund_id) != graph.resolve(fund_id):
            raise ValueError("fund_id conflicts with project_scope")
        ingestion = Ingestion(graph, store, use_gemini=request.use_gemini, fund_id=fund_id,
                              snapshot_as_of=request.snapshot_as_of.isoformat() if request.snapshot_as_of else None)
        sources = [ingestion.ingest(item) for item in items]
        if request.project_scope:
            for source_id in sources:
                scope = request.project_scope
                project_id = graph.upsert("project", scope.quarter + " " + scope.workflow_type, source_id,
                                          **scope.model_dump())
                project = graph.state.entities[project_id]
                graph.edge(project_id, "for_fund", project.fund_id, source_id)
                graph.edge(project_id, "for_company", project.management_company_id, source_id)
                graph.edge(source_id, "part_of", project_id, source_id)
    except (ValueError, OverflowError, KeyError) as error:
        raise HTTPException(422, str(error)) from None
    except Exception:
        # Do not log provider HTTP exceptions: they can include query content.
        raise HTTPException(503, "Connector or extraction failed; verify server credentials and retry this page") from None
    finally:
        connector.close()
    save(store, graph)
    return {"source_ids": sources, "next_page_token": next_token, "revision": graph.state.revision,
            "warnings": {sid: graph.state.sources[sid].warnings for sid in sources if graph.state.sources[sid].warnings}}


@router.get("/graph/entities")
def entities(kind: Literal["person", "company", "fund", "project"] | None = None,
             offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)):
    _, graph = load()
    rows = sorted((e for e in graph.state.entities.values() if not e.merged_into and (kind is None or e.kind == kind)), key=lambda e: e.key)
    end = offset + limit
    return {"entities": [e.model_dump(mode="json") for e in rows[offset:end]],
            "next_offset": end if end < len(rows) else None}


@router.get("/graph/entities/{entity_id}")
def entity(entity_id: str, as_of: date | None = None):
    _, graph = load()
    if entity_id not in graph.state.entities:
        raise HTTPException(404, "Entity not found")
    return graph.flatten(entity_id, as_of)


@router.get("/graph/sources")
def sources(offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)):
    _, graph = load()
    rows = sorted(graph.state.sources.values(), key=lambda s: s.key)
    end = offset + limit
    return {"sources": [s.model_dump(mode="json", exclude={"text"}) for s in rows[offset:end]],
            "next_offset": end if end < len(rows) else None}


@router.get("/graph/sources/{source_id}")
def source(source_id: str):
    _, graph = load()
    if source_id not in graph.state.sources:
        raise HTTPException(404, "Source not found")
    return graph.state.sources[source_id].model_dump(mode="json")


@router.post("/graph/entities")
def upsert(request: EntityRequest):
    store, graph = load()
    fields = request.entity.model_dump(mode="json", exclude={"key", "kind", "name", "sources", "merged_into"})
    try:
        entity_id = graph.upsert(request.entity.kind, request.entity.name, request.source_id, **fields)
        if request.entity.kind == "project":
            project = graph.state.entities[entity_id]
            graph.edge(entity_id, "for_fund", project.fund_id, request.source_id)
            graph.edge(entity_id, "for_company", project.management_company_id, request.source_id)
            graph.edge(request.source_id, "part_of", entity_id, request.source_id)
    except (ValueError, KeyError) as error:
        raise HTTPException(422, str(error)) from None
    save(store, graph)
    return graph.flatten(entity_id)


@router.post("/graph/merge")
def merge(request: MergeRequest):
    store, graph = load()
    try:
        entity_id = graph.merge(request.keep, request.duplicate, request.evidence_source_id)
    except (ValueError, KeyError) as error:
        raise HTTPException(422, str(error)) from None
    save(store, graph)
    return graph.flatten(entity_id)


@router.post("/graph/sources/{source_id}/accept-proposals")
def accept_proposals(source_id: str):
    store, graph = load()
    try:
        Ingestion(graph).accept_proposals(source_id)
    except (ValueError, KeyError) as error:
        raise HTTPException(422, str(error)) from None
    save(store, graph)
    return {"source_id": source_id, "status": "accepted"}


@router.get("/graph/funds/{fund_id}/terms")
def fund_terms(fund_id: str, as_of: date):
    from app.terms import terms_as_of
    _, graph = load()
    if fund_id not in graph.state.entities or graph.state.entities[fund_id].kind != "fund":
        raise HTTPException(404, "Fund not found")
    try:
        return terms_as_of(graph, fund_id, as_of)
    except ValueError as error:
        raise HTTPException(409, str(error)) from None
