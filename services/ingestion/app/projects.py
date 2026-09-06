"""Copy evidence into a project; all subsequent workflow reads are project-local."""
import csv
import io
import json
from datetime import date

from app.graph import ENTITIES, Graph, Source, key, now
from app.project_store import ProjectStore, artifact, link
from app.terms import terms_as_of


def materialize(canonical_store, project_id, source_ids, project_store=None):
    graph = canonical_store.load_graph()
    project_id = graph.resolve(project_id)
    project = graph.state.entities[project_id]
    if project.kind != "project":
        raise ValueError("Expected canonical project")
    selected = set(source_ids)
    selected.update(e.subject for e in graph.state.edges.values()
                    if e.predicate == "part_of" and e.object == project_id and e.subject in graph.state.sources)
    if not selected or not selected <= graph.state.sources.keys():
        raise ValueError("Select existing source nodes for this project")
    # Include document attachments and structured rows, plus their owning source.
    # Do not traverse arbitrary entity relationships into unrelated projects.
    while True:
        expanded = set(selected)
        for edge in graph.state.edges.values():
            if edge.predicate in {"attached_to", "received_via", "part_of"}:
                if edge.subject in graph.state.sources and edge.object in graph.state.sources:
                    if edge.subject in selected or edge.object in selected:
                        expanded.update((edge.subject, edge.object))
        if expanded == selected:
            break
        selected = expanded
    entities = {project_id, project.fund_id, project.management_company_id}
    for edge in graph.state.edges.values():
        if edge.source_id in selected:
            for endpoint in (edge.subject, edge.object):
                if endpoint in graph.state.entities and graph.state.entities[endpoint].kind != "project":
                    entities.add(endpoint)
                    entities.add(graph.resolve(endpoint))
    nodes = [graph.state.entities[entity_id].model_dump(mode="json") for entity_id in sorted(entities)]
    # Freeze local references; retain origin IDs only as provenance strings.
    for node in nodes:
        node["origin_sources"] = node["sources"]
        node["sources"] = [sid for sid in node["sources"] if sid in selected]
    nodes.extend(graph.state.sources[sid].model_dump(mode="json") for sid in sorted(selected))
    links = []
    included = selected | entities
    for edge in graph.state.edges.values():
        if edge.subject in included and edge.object in included and edge.source_id in selected:
            links.append(edge.model_dump(mode="json"))
    artifacts, missing = [], []
    for source_id in sorted(selected):
        source = graph.state.sources[source_id]
        content = canonical_store.get_source_bytes(source_id)
        if content is not None:
            import hashlib
            if hashlib.sha256(content).hexdigest() != source.sha256:
                raise ValueError("Canonical source checksum mismatch: " + source_id)
            item = artifact(source.filename, content, source_ids=[source_id], role="original")
            artifacts.append(item)
            links.append(link(item["key"], "evidence_for", source_id))
        elif source.kind != "record":
            missing.append(source_id)
        # Text is a separately identified derivative, never mislabeled original bytes.
        item = artifact(source.filename + ".extracted.txt", source.text.encode(), source_ids=[source_id], role="extracted_text")
        artifacts.append(item)
        links.append(link(item["key"], "derived_from", source_id))
        if source.document_id:
            document = canonical_store.get(source.document_id)
            if document:
                item = artifact(source.filename + ".context.json", json.dumps(document, sort_keys=True).encode(),
                                source_ids=[source_id], role="parsed_context")
                artifacts.append(item)
                links.append(link(item["key"], "derived_from", source_id))
    seed_id = key(project_id, graph.state.revision, sorted(selected))
    seed = {"key": seed_id, "kind": "materialization", "canonical_revision": graph.state.revision,
            "source_ids": sorted(selected), "missing_originals": missing, "recorded_at": now()}
    nodes.append(seed)
    links.extend(link(seed_id, "copied", node_id) for node_id in sorted(included))
    target = project_store or ProjectStore.provision(project_id)
    target.initialize(project.model_dump(mode="json"), graph.state.revision)
    target.bundle(nodes=nodes, artifacts=artifacts, links=links)
    return {"project_id": project_id, "materialization_id": seed_id, "sources": len(selected),
            "artifacts": [{k: item[k] for k in ("key", "filename", "role", "source_ids")} for item in artifacts],
            "missing_originals": missing}


def all_records(store, table):
    offset = 0
    while True:
        page = store.list_records(table, offset, 200)
        yield from page
        if len(page) < 200:
            break
        offset += len(page)


def snapshot(store, as_of):
    graph = Graph()
    for record in all_records(store, "node"):
        record = {k: v for k, v in record.items() if k not in {"id", "origin_sources"}}
        if record.get("kind") in {"person", "company", "fund", "project"}:
            entity = ENTITIES.validate_python(record)
            graph.state.entities[entity.key] = entity
        elif record.get("kind") in {"file", "email", "attachment", "record"}:
            source = Source.model_validate(record)
            graph.state.sources[source.key] = source
    fund_id = store.manifest()["project"]["fund_id"]
    result = terms_as_of(graph, fund_id, as_of)
    if not result["rows"]:
        raise ValueError("No applicable project-local terms; materialize scoped terms source rows first")
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=list(result["rows"][0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(result["rows"])
    inputs = sorted({source_id for ids in result["provenance"].values() for source_id in ids})
    item = artifact("terms-" + as_of.isoformat() + ".csv", stream.getvalue().encode(),
                    source_ids=inputs, role="terms_snapshot")
    store.bundle(artifacts=[item], links=[link(item["key"], "derived_from", source_id) for source_id in inputs])
    return {k: v for k, v in item.items() if k != "base64"}


def ratify(store, artifact_id, actor, evidence_ids, reason):
    if not actor.strip() or not reason.strip() or not evidence_ids:
        raise ValueError("Ratification needs actor, reason and evidence")
    item, _ = store.read_artifact(artifact_id)
    for evidence_id in evidence_ids:
        if not store.get_record("node", evidence_id):
            raise ValueError("Ratification evidence must exist in the project graph")
    decision = {"key": key("ratification", artifact_id, actor, sorted(evidence_ids), reason),
                "kind": "ratification", "artifact_id": artifact_id, "sha256": item["sha256"],
                "actor": actor, "reason": reason, "evidence_ids": sorted(evidence_ids), "recorded_at": now()}
    store.bundle(decisions=[decision], links=[link(decision["key"], "ratifies", artifact_id)] +
                 [link(decision["key"], "cites", evidence_id) for evidence_id in evidence_ids])
    return decision
