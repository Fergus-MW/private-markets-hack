"""Fixed canonical schemas and conservative, source-grounded entity resolution."""
import hashlib
import json
import re
from datetime import date, datetime, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator


def key(*parts):
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Canonical(Strict):
    key: str = ""
    name: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    external_ids: dict[str, str] = Field(default_factory=dict)
    sources: list[str] = Field(default_factory=list)
    merged_into: str | None = None


class Person(Canonical):
    kind: Literal["person"] = "person"
    emails: list[str] = Field(default_factory=list)
    contact_type: Literal["person", "mailbox", "unknown"] = "unknown"


class Company(Canonical):
    kind: Literal["company"] = "company"
    registration_number: str | None = None
    jurisdiction: str | None = None
    lei: str | None = None
    domains: list[str] = Field(default_factory=list)


class Fund(Canonical):
    kind: Literal["fund"] = "fund"
    lei: str | None = None
    currency: str | None = None
    domicile: str | None = None


class Project(Canonical):
    kind: Literal["project"] = "project"
    fund_id: str
    management_company_id: str
    quarter: str = Field(pattern=r"^\d{4}-Q[1-4]$")
    workflow_type: str = Field(min_length=1)
    status: Literal["in_progress", "completed"] = "in_progress"
    completed_at: datetime | None = None
    completion_source_id: str | None = None

    @model_validator(mode="after")
    def evidence_required(self):
        if self.status == "completed" and not (self.completed_at and self.completion_source_id):
            raise ValueError("Completed projects require timestamp and source evidence")
        return self


Entity = Annotated[Person | Company | Fund | Project, Field(discriminator="kind")]
ENTITIES = TypeAdapter(Entity)


class Source(Strict):
    key: str
    kind: Literal["email", "file", "attachment", "record"]
    provider: str
    account: str
    external_id: str
    revision: str
    filename: str
    sha256: str
    text: str = Field(max_length=1_000_000)
    metadata: dict = Field(default_factory=dict)
    document_id: str | None = None
    recorded_at: str = Field(default_factory=now)
    warnings: list[str] = Field(default_factory=list)


class Edge(Strict):
    key: str
    subject: str
    predicate: Literal["sent", "received", "attached_to", "mentions", "works_for",
                       "manages", "invests_in", "for_fund", "for_company", "part_of", "received_via"]
    object: str
    source_id: str
    valid_from: date | None = None
    valid_to: date | None = None
    method: str = "programmatic"


class GraphState(Strict):
    revision: int = 0
    entities: dict[str, Entity] = Field(default_factory=dict)
    sources: dict[str, Source] = Field(default_factory=dict)
    edges: dict[str, Edge] = Field(default_factory=dict)
    issues: list[dict] = Field(default_factory=list)


class IdentityConflict(ValueError):
    pass


class Graph:
    def __init__(self, state=None):
        self.state = state or GraphState()

    def resolve(self, entity_id):
        seen = set()
        while self.state.entities[entity_id].merged_into:
            if entity_id in seen:
                raise ValueError("Identity redirect cycle")
            seen.add(entity_id)
            entity_id = self.state.entities[entity_id].merged_into
        return entity_id

    @staticmethod
    def identities(entity):
        ids = {(entity.kind, "external:" + ns, value.strip())
               for ns, value in entity.external_ids.items() if value.strip()}
        if isinstance(entity, Person):
            ids |= {(entity.kind, "email", email.casefold().strip()) for email in entity.emails}
        if isinstance(entity, (Company, Fund)) and entity.lei:
            ids.add((entity.kind, "lei", entity.lei.upper()))
        if isinstance(entity, Company) and entity.registration_number and entity.jurisdiction:
            ids.add((entity.kind, "registration", entity.jurisdiction.upper() + ":" + entity.registration_number))
        if isinstance(entity, Project):
            ids.add(("project", "scope", key(entity.fund_id, entity.management_company_id,
                                             entity.quarter, entity.workflow_type)))
        return ids

    def upsert(self, kind, name, source_id, **fields):
        if source_id not in self.state.sources:
            raise ValueError("Entity requires an existing source")
        fields["external_ids"] = {ns.strip(): value.strip() for ns, value in fields.get("external_ids", {}).items()}
        if any(not ns or not value for ns, value in fields["external_ids"].items()):
            raise ValueError("External IDs require a namespace and value")
        if fields.get("lei"):
            fields["lei"] = fields["lei"].strip().upper()
        if fields.get("jurisdiction"):
            fields["jurisdiction"] = fields["jurisdiction"].strip().upper()
        if kind == "person":
            fields["emails"] = sorted({x.casefold().strip() for x in fields.get("emails", [])})
            if any(not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", x) for x in fields["emails"]):
                raise ValueError("Invalid email identity")
        if kind == "project":
            for field, expected in (("fund_id", "fund"), ("management_company_id", "company")):
                fields[field] = self.resolve(fields[field])
                if self.state.entities[fields[field]].kind != expected:
                    raise ValueError("Invalid project scope")
            if fields.get("completion_source_id") and fields["completion_source_id"] not in self.state.sources:
                raise ValueError("Missing completion evidence")
        candidate = ENTITIES.validate_python(dict(kind=kind, key="pending", name=name.strip(), sources=[source_id], **fields))
        identities = self.identities(candidate)
        matches = [e for e in self.state.entities.values()
                   if not e.merged_into and identities & self.identities(e)]
        if len(matches) > 1:
            raise IdentityConflict("Identifiers point to multiple entities; explicit merge required")
        if matches:
            current = matches[0]
            self._combine(current, candidate)
            return current.key
        candidate.key = key(kind, sorted(identities) if identities else (source_id, name.casefold().strip()))
        if candidate.key in self.state.entities:
            self._combine(self.state.entities[candidate.key], candidate)
        else:
            self.state.entities[candidate.key] = candidate
        return candidate.key

    def _combine(self, current, incoming):
        # Conflicting strong identifiers cannot silently overwrite canonical identity.
        if isinstance(current, Project):
            scope = ("fund_id", "management_company_id", "quarter", "workflow_type")
            if any(getattr(current, field) != getattr(incoming, field) for field in scope):
                raise IdentityConflict("Cannot merge different project scopes")
        for namespace, value in incoming.external_ids.items():
            if namespace in current.external_ids and current.external_ids[namespace] != value:
                raise IdentityConflict("Conflicting external ID: " + namespace)
        for field in ("lei", "registration_number", "jurisdiction"):
            a, b = getattr(current, field, None), getattr(incoming, field, None)
            if a and b and a != b:
                raise IdentityConflict("Conflicting identity field: " + field)
        current.aliases = sorted(set(current.aliases + incoming.aliases + [incoming.name]) - {current.name})
        current.sources = sorted(set(current.sources + incoming.sources))
        current.external_ids.update(incoming.external_ids)
        for field in ("emails", "domains"):
            if hasattr(current, field):
                setattr(current, field, sorted(set(getattr(current, field) + getattr(incoming, field))))
        for field in ("lei", "registration_number", "jurisdiction", "currency", "domicile"):
            if hasattr(current, field) and not getattr(current, field):
                setattr(current, field, getattr(incoming, field))
        if isinstance(current, Project) and incoming.status == "completed":
            current.status, current.completed_at = incoming.status, incoming.completed_at
            current.completion_source_id = incoming.completion_source_id

    def edge(self, subject, predicate, object_id, source_id, **fields):
        nodes = self.state.entities.keys() | self.state.sources.keys()
        if subject not in nodes or object_id not in nodes or source_id not in self.state.sources:
            raise ValueError("Dangling graph edge")
        edge_id = key(subject, predicate, object_id, source_id, fields)
        self.state.edges[edge_id] = Edge(key=edge_id, subject=subject, predicate=predicate,
                                        object=object_id, source_id=source_id, **fields)

    def merge(self, keep, duplicate, evidence_source_id):
        keep, duplicate = self.resolve(keep), self.resolve(duplicate)
        if keep == duplicate:
            return keep
        a, b = self.state.entities[keep], self.state.entities[duplicate]
        if a.kind != b.kind or evidence_source_id not in self.state.sources:
            raise ValueError("Merge requires same entity type and evidence")
        if isinstance(a, Project) and self.identities(a) != self.identities(b):
            raise ValueError("Cannot merge different project scopes")
        self._combine(a, b)
        b.merged_into = keep
        a.sources = sorted(set(a.sources + [evidence_source_id]))
        self.state.issues.append({"type": "merge", "keep": keep, "duplicate": duplicate,
                                  "source_id": evidence_source_id, "recorded_at": now()})
        # Keep historical edges; canonical flattening resolves their endpoints.
        for project in list(self.state.entities.values()):
            if isinstance(project, Project) and not project.merged_into:
                project.fund_id = self.resolve(project.fund_id)
                project.management_company_id = self.resolve(project.management_company_id)
        projects = {}
        for project in list(self.state.entities.values()):
            if isinstance(project, Project) and not project.merged_into:
                scope = (project.fund_id, project.management_company_id, project.quarter, project.workflow_type)
                if scope in projects:
                    self._combine(projects[scope], project)
                    project.merged_into = projects[scope].key
                else:
                    projects[scope] = project
        return keep

    def flatten(self, entity_id, as_of=None):
        entity_id = self.resolve(entity_id)
        entity = self.state.entities[entity_id].model_dump(mode="json")
        edges = []
        for edge in self.state.edges.values():
            item = edge.model_dump(mode="json")
            for field in ("subject", "object"):
                if item[field] in self.state.entities:
                    item[field] = self.resolve(item[field])
            if entity_id in (item["subject"], item["object"]):
                if as_of and ((edge.valid_from and edge.valid_from > as_of) or
                              (edge.valid_to and edge.valid_to < as_of)):
                    continue
                edges.append(item)
        return {"entity": entity, "relationships": edges}
