"""Project graph and workflow orchestration endpoints under existing service IAM."""
import json
import logging
import mimetypes
from datetime import date
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import Field

from app.graph import Strict
from app.project_store import ProjectStore, project_database
from app.projects import materialize, ratify, snapshot
from app.store import GraphStore
from app.workflows import run_workflow

router = APIRouter(prefix="/projects", tags=["project workflows"])
logger = logging.getLogger(__name__)


def public_run(value):
    return {key: item for key, item in value.items() if key != "claim_token"}


class MaterializeRequest(Strict):
    source_ids: list[str] = Field(default_factory=list, max_length=200)


class RunRequest(Strict):
    gate: Literal["terms", "loader"]
    mode: Literal["terms", "arithmetic-only", "loader"]
    inputs: dict[str, str]
    ratifications: dict[str, str] = Field(default_factory=dict)


class AutomateRequest(Strict):
    gate: Literal["terms", "loader"]
    mode: Literal["terms", "arithmetic-only", "loader"]
    source_inputs: dict[str, str]
    evidence_source_ids: list[str] = Field(default_factory=list, max_length=200)
    ratifications: dict[str, str] = Field(default_factory=dict)


class RatifyRequest(Strict):
    artifact_id: str
    actor: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1)


class SnapshotRequest(Strict):
    as_of: date


def local_store(project_id):
    try:
        project_database(project_id)
        store = ProjectStore(project_id)
        if not store.manifest():
            raise HTTPException(404, "Project has not been materialized")
        return store
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, "Project unavailable; verify workflow database configuration") from None


@router.post("/{project_id}/materialize")
def materialize_project(project_id: str, request: MaterializeRequest):
    try:
        project_database(project_id)
        return materialize(GraphStore(), project_id, request.source_ids)
    except (ValueError, KeyError) as error:
        raise HTTPException(422, str(error)) from None
    except Exception:
        logger.exception("Project materialization failed")
        raise HTTPException(503, "Project materialization failed; retry safely") from None


@router.get("/{project_id}")
def project(project_id: str):
    return local_store(project_id).manifest()


@router.get("/{project_id}/graph")
def graph(project_id: str, table: Literal["node", "link"] = "node",
          offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=200)):
    rows = local_store(project_id).list_records(table, offset, limit + 1)
    return {"records": rows[:limit], "next_offset": offset + limit if len(rows) > limit else None}


@router.get("/{project_id}/artifacts")
def artifacts(project_id: str, offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=200)):
    rows = local_store(project_id).list_records("artifact", offset, limit + 1)
    return {"artifacts": rows[:limit], "next_offset": offset + limit if len(rows) > limit else None}


@router.get("/{project_id}/artifacts/{artifact_id}")
def download(project_id: str, artifact_id: str):
    try:
        item, content = local_store(project_id).read_artifact(artifact_id)
    except KeyError:
        raise HTTPException(404, "Artifact not found in this project") from None
    return Response(content, media_type=mimetypes.guess_type(item["filename"])[0] or "application/octet-stream",
                    headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(item["filename"], safe="")})


@router.post("/{project_id}/terms-snapshot")
def terms_snapshot(project_id: str, request: SnapshotRequest):
    try:
        return snapshot(local_store(project_id), request.as_of)
    except (ValueError, KeyError) as error:
        raise HTTPException(422, str(error)) from None


@router.post("/{project_id}/ratifications")
def ratification(project_id: str, request: RatifyRequest):
    try:
        from app.identity import current_identity
        identity = current_identity.get()
        actor = identity['actor'] if identity else request.actor
        return ratify(local_store(project_id), request.artifact_id, actor, request.evidence_ids, request.reason)
    except (ValueError, KeyError) as error:
        raise HTTPException(422, str(error)) from None


@router.post("/{project_id}/runs")
def run(project_id: str, request: RunRequest):
    try:
        return public_run(run_workflow(local_store(project_id), request.gate, request.mode, request.inputs, request.ratifications))
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    except Exception:
        raise HTTPException(409, "Run could not be claimed; retry after the active run finishes") from None


@router.get("/{project_id}/runs")
def runs(project_id: str, offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=200)):
    rows = local_store(project_id).list_records("run", offset, limit + 1)
    return {"runs": [{k: v for k, v in row.items() if k != "claim_token"} for row in rows[:limit]],
            "next_offset": offset + limit if len(rows) > limit else None}


def normalized_check(check):
    """One shape for both checkers: the terms gate names investors and amounts, the
    loader gate names a group and an observed/expected pair."""
    if "name" in check:
        return {"id": check.get("check", ""), "tier": check.get("tier", "c"), "status": check["status"],
                "name": check["name"], "who": check.get("investors", ""),
                "amount": float(check.get("amount") or 0), "detail": check.get("detail", "")}
    return {"id": check.get("id", ""), "tier": check.get("tier", "c"), "status": check["status"],
            "name": check.get("check", ""), "who": check.get("group", ""), "amount": 0.0,
            "detail": f"observed {check.get('observed')}; expected {check.get('expected')}"}


def amount_at_stake(checks):
    # Tier a only: tier b findings are components of the same money (gates/README.md).
    return round(sum(item["amount"] for item in checks if item["status"] == "FAIL" and item["tier"] == "a"), 2)


def gate_run(store, run):
    output = run.get("output") or {}
    results, checks = {}, []
    identifier = (output.get("artifacts") or {}).get("check_results")
    if identifier:
        try:
            results = json.loads(store.read_artifact(identifier)[1])
            checks = [normalized_check(check) for check in results.get("checks", [])]
        except (KeyError, ValueError):
            results = {}
    inputs = {}
    for role, artifact_id in (run.get("inputs") or {}).items():
        item = store.get_record("artifact", artifact_id) or {}
        inputs[role] = {"artifact_id": artifact_id, "filename": item.get("filename", ""),
                        "sha256": item.get("sha256", "")}
    return {"run_id": run["key"], "turn": run.get("turn"), "status": run["status"],
            "gate": run["gate"], "mode": run["mode"], "as_of": run.get("as_of") or results.get("as_of", ""),
            "started_at": run.get("started_at", ""), "finished_at": run.get("finished_at", ""),
            "entity": results.get("entity", ""), "terms_rows_in_force": results.get("terms_rows_in_force"),
            "summary": results.get("summary", {}), "findings_by_tier": results.get("findings_by_tier", {}),
            "amount_at_stake": results.get("amount_at_stake", amount_at_stake(checks)),
            "release_ready": output.get("release_ready", False), "reason": output.get("reason", ""),
            "checks": checks, "inputs": inputs, "runtime": run.get("runtime", {}),
            "artifacts": output.get("artifacts", {})}


@router.get("/{project_id}/dashboard")
def dashboard(project_id: str, limit: int = Query(20, ge=1, le=100)):
    """Every deterministic gate run of this project, as the QC dashboard renders it.
    Agent-team runs are excluded: they have no checks, only a written summary."""
    store = local_store(project_id)
    runs = [row for row in store.list_records("run", 0, 200) if row.get("gate")]
    runs.sort(key=lambda row: row.get("started_at", ""), reverse=True)
    return {"project": store.manifest()["project"], "runs": [gate_run(store, row) for row in runs[:limit]]}


@router.post("/{project_id}/automate")
def automate(project_id: str, request: AutomateRequest):
    seed = materialize_project(project_id, MaterializeRequest(source_ids=sorted(
        set(request.source_inputs.values()) | set(request.evidence_source_ids))))
    project_id = seed["project_id"]
    store = local_store(project_id)
    by_source = {source_id: item["key"] for item in seed["artifacts"] if item["role"] == "original"
                 for source_id in item["source_ids"]}
    inputs = {role: by_source[source_id] for role, source_id in request.source_inputs.items() if source_id in by_source}
    if request.gate == "terms" and request.mode == "terms" and "terms" not in inputs:
        from app.workflows import quarter_bounds
        try:
            item = snapshot(store, quarter_bounds(store.manifest()["project"]["quarter"])[1])
            inputs["terms"] = item["key"]
        except ValueError:
            pass  # The persisted coverage result will name the missing terms input.
    return {"materialization": seed, "inputs": inputs,
            "run": public_run(run_workflow(store, request.gate, request.mode, inputs, request.ratifications))}
