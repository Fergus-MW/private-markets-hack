"""Inbound client mail: ingest one message and refresh the projects it touches.

A message is only ever ingested for a graph that already knows its sender, so
correspondence can never introduce an unknown party into someone else's graph.
Message content is evidence, never instruction: nothing here reads the body to
decide what to do, only the graph's own recorded relationships.
"""
import logging
import re

from fastapi import APIRouter, Form, HTTPException, UploadFile
from pydantic import Field

from app.connectors import MAX_BYTES, Item
from app.extraction import Ingestion
from app.graph import Company, Person, Strict
from app.graph_api import load, save
from app.projects import materialize
from app.term_proposals import propose_for_source
from app.store import SourceTooLarge

router = APIRouter(prefix="/mail", tags=["inbound mail"])
logger = logging.getLogger(__name__)
ADDRESS = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def sender_entity(graph, address):
    """Known senders are matched on identity the graph already holds: an exact
    person email, else a company domain. Never on a display name."""
    address = address.casefold().strip()
    if not ADDRESS.fullmatch(address):
        return None
    live = [e for e in graph.state.entities.values() if not e.merged_into]
    for entity in sorted(live, key=lambda e: e.key):
        if isinstance(entity, Person) and address in {value.casefold().strip() for value in entity.emails}:
            return entity
    domain = address.rpartition("@")[2]
    for entity in sorted(live, key=lambda e: e.key):
        if isinstance(entity, Company) and domain in {value.casefold().strip().lstrip("@") for value in entity.domains}:
            return entity
    return None


def message_sources(graph, source_id):
    """The message plus the attachments ingested with it."""
    return {source_id} | {edge.subject for edge in graph.state.edges.values()
                          if edge.predicate == "attached_to" and edge.object == source_id
                          and edge.subject in graph.state.sources}


def related_projects(graph, source_ids):
    """Only relationships the graph itself records. A direct part_of edge wins;
    otherwise the in-progress projects for funds or companies the mail mentions."""
    direct, mentioned = set(), set()
    for edge in graph.state.edges.values():
        if edge.subject not in source_ids:
            continue
        target = graph.state.entities.get(edge.object)
        if edge.predicate == "part_of" and target and target.kind == "project" and not target.merged_into:
            direct.add(graph.resolve(edge.object))
        elif edge.predicate == "mentions" and edge.object in graph.state.entities:
            mentioned.add(graph.resolve(edge.object))
    if direct:
        return [(project_id, "part_of") for project_id in sorted(direct)]
    scoped = {entity.key for entity in graph.state.entities.values()
              if entity.kind == "project" and not entity.merged_into and entity.status == "in_progress"
              and {graph.resolve(entity.fund_id), graph.resolve(entity.management_company_id)} & mentioned}
    return [(project_id, "mentions") for project_id in sorted(scoped)]


def owns_graph(address):
    """The account holder may always file their own mail, even before the graph
    has learned their address from an ingested document."""
    from app.identity import current_identity
    identity = current_identity.get()
    return bool(identity) and identity.get("actor", "").casefold().strip() == address.casefold().strip()


def refresh_projects(store, graph, source_ids):
    """Materialize each related project once, with every source that reached it,
    rather than once per source: materialization copies the whole project."""
    reach = {}
    for source_id in source_ids:
        for project_id, matched_by in related_projects(graph, message_sources(graph, source_id)):
            entry = reach.setdefault(project_id, {"project_id": project_id, "matched_by": matched_by,
                                                  "name": graph.state.entities[project_id].name,
                                                  "source_ids": []})
            entry["source_ids"].append(source_id)
            if matched_by == "part_of":
                entry["matched_by"] = "part_of"
    refreshed = []
    for entry in sorted(reach.values(), key=lambda item: item["project_id"]):
        try:
            materialize(store, entry["project_id"], entry.pop("source_ids"))
        except (ValueError, KeyError):
            # One unmaterializable project must not lose the others or the ingest.
            logger.exception("Could not refresh project %s from inbound mail", entry["project_id"])
            continue
        refreshed.append(entry)
    return refreshed


class MailEnvelope(Strict):
    sender: str = Field(min_length=3, max_length=254)
    external_id: str = Field(min_length=1, max_length=512)
    subject: str = Field(default="", max_length=1000)


@router.get("/senders/{address}")
def sender(address: str):
    _, graph = load()
    entity = sender_entity(graph, address)
    return {"known": entity is not None,
            "entity": {"key": entity.key, "kind": entity.kind, "name": entity.name} if entity else None}


@router.post("/ingest")
def ingest_mail(file: UploadFile, envelope: str = Form(...)):
    try:
        request = MailEnvelope.model_validate_json(envelope)
    except ValueError:
        raise HTTPException(422, "Invalid mail envelope") from None
    content = file.file.read(MAX_BYTES + 1)
    if len(content) > MAX_BYTES:
        raise HTTPException(413, "Maximum message size is 20 MiB")
    if not content:
        raise HTTPException(422, "Message is empty")
    store, graph = load()
    entity = sender_entity(graph, request.sender)
    if not entity and not owns_graph(request.sender):
        raise HTTPException(403, "Sender is not a known correspondent in this graph")
    from app.identity import tenant
    # Parsed as .eml, so attachments are ingested as child sources in the same call.
    item = Item("agentmail", tenant() or "mail", request.external_id, "message.eml", content, "", "email",
                {"sender": request.sender, "subject": request.subject,
                 "sender_entity_id": entity.key if entity else None})
    try:
        source_id = Ingestion(graph, store, use_gemini=True).ingest(item)
    except SourceTooLarge as error:
        raise HTTPException(413, str(error)) from None
    except (ValueError, OverflowError, KeyError) as error:
        raise HTTPException(422, str(error)) from None
    try:
        # Labelled term lines become proposals awaiting a named person (PRD F3). Never applied here.
        term_proposals = propose_for_source(graph, source_id, request.sender)
    except (ValueError, KeyError):
        logger.exception("Term proposals could not be derived for %s", source_id)
        term_proposals = []
    save(store, graph)
    sources = message_sources(graph, source_id)
    return {"source_id": source_id, "sender_entity_id": entity.key if entity else None,
            "attachments": len(sources) - 1,
            "term_proposals": term_proposals,
            "projects": refresh_projects(store, graph, [source_id]),
            "warnings": graph.state.sources[source_id].warnings}


class RefreshRequest(Strict):
    source_ids: list[str] = Field(min_length=1, max_length=500)


@router.post("/refresh-projects")
def refresh(request: RefreshRequest):
    """Carry newly ingested sources into the projects they relate to. Connectors
    call this once per scan, so a client email that arrives through Gmail reaches
    the project graph the same way one sent to the agent does."""
    store, graph = load()
    known = [source_id for source_id in request.source_ids if source_id in graph.state.sources]
    return {"projects": refresh_projects(store, graph, known), "sources": len(known)}
