"""Term-amendment proposals: list, (re)propose for a message, ratify. Under existing service IAM."""
from datetime import date

from fastapi import APIRouter, HTTPException, Query
from pydantic import Field

from app.graph import Strict
from app.graph_api import load, save
from app.term_proposals import list_proposals, propose_for_source, ratify
from app.terms import terms_as_of

router = APIRouter(prefix="/graph/term-proposals", tags=["term proposals"])


class RatifyTermRequest(Strict):
    actor: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class ProposeRequest(Strict):
    sender: str | None = None
    as_of: date | None = None


@router.get("")
def proposals(status: str | None = Query(None)):
    _, graph = load()
    return {"proposals": list_proposals(graph, status)}


@router.post("/{source_id}/propose")
def propose(source_id: str, request: ProposeRequest):
    store, graph = load()
    if source_id not in graph.state.sources:
        raise HTTPException(404, "Source not found in your workspace")
    result = propose_for_source(graph, source_id, request.sender, request.as_of)
    save(store, graph)
    return {"source_id": source_id, "proposals": result}


@router.post("/{source_id}/{proposal_id}/ratify")
def ratify_proposal(source_id: str, proposal_id: str, request: RatifyTermRequest):
    store, graph = load()
    from app.identity import current_identity
    identity = current_identity.get()
    actor = identity["actor"] if identity else request.actor
    try:
        result = ratify(graph, source_id, proposal_id, actor, request.reason)
    except KeyError as error:
        raise HTTPException(404, str(error)) from None
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    save(store, graph)
    fund_id = result["proposal"]["fund_id"]
    in_force = terms_as_of(graph, fund_id, date.today())
    return {**result, "terms_now": {"fund_id": fund_id, "as_of": in_force["as_of"],
                                    "row": next((r for r in in_force["rows"] if r["investor_id"] == result["proposal"]["investor_id"]), None)}}
