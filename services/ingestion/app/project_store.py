"""Project database boundary. Runtime queries use a database-scoped principal."""
import base64
import hashlib
import hmac
import os
import re

from app.graph import key, now
from app.store import Store


def project_database(project_id):
    if not re.fullmatch(r"[a-f0-9]{64}", project_id):
        raise ValueError("Expected canonical project key")
    from app.identity import tenant
    if tenant():
        return 'project_' + hashlib.sha256((tenant() + ':' + project_id).encode()).hexdigest()
    return "project_" + project_id


def database_password(database):
    return hmac.new(os.environ['SURREAL_PROJECT_SECRET'].encode(), database.encode(), hashlib.sha256).hexdigest()


def provision_database(database):
    if not re.fullmatch(r'(?:user|project)_[a-f0-9]{64}', database):
        raise ValueError('Invalid isolated database name')
    admin = Store(namespace='projects', database='catalog', user=os.environ['SURREAL_PROJECT_ADMIN_USER'],
                  password=os.environ['SURREAL_PROJECT_ADMIN_PASSWORD'],
                  auth_level=os.environ.get('SURREAL_PROJECT_ADMIN_AUTH_LEVEL', 'namespace'))
    password = database_password(database)
    admin.query(f"DEFINE DATABASE IF NOT EXISTS {database}; USE DB {database}; "
                f"DEFINE USER IF NOT EXISTS workflow ON DATABASE PASSWORD '{password}' ROLES EDITOR;")


def project_password(project_id):
    from app.identity import tenant
    if tenant():
        return database_password(project_database(project_id))
    return hmac.new(os.environ["SURREAL_PROJECT_SECRET"].encode(), project_id.encode(), hashlib.sha256).hexdigest()


class ProjectStore(Store):
    def __init__(self, project_id):
        self.project_id = project_id
        super().__init__(database=project_database(project_id), namespace="projects", user="workflow",
                         password=project_password(project_id), auth_level="database")

    @classmethod
    def provision(cls, project_id):
        from app.identity import tenant
        if tenant():
            provision_database(project_database(project_id))
            return cls(project_id)
        database = project_database(project_id)
        password = project_password(project_id)
        # Only the provisioning path has namespace privileges. Never fall back to
        # the main ingestion identity or expose a caller-selected DB/query string.
        admin = Store(namespace="projects", database="catalog", user=os.environ["SURREAL_PROJECT_ADMIN_USER"],
                      password=os.environ["SURREAL_PROJECT_ADMIN_PASSWORD"],
                      auth_level=os.environ.get("SURREAL_PROJECT_ADMIN_AUTH_LEVEL", "namespace"))
        admin.query(f"DEFINE DATABASE IF NOT EXISTS {database}; USE DB {database}; "
                    f"DEFINE USER IF NOT EXISTS workflow ON DATABASE PASSWORD '{password}' ROLES EDITOR;")
        return cls(project_id)

    def get_record(self, table, record_id):
        if table not in {"manifest", "node", "artifact", "run", "decision", "blob"}:
            raise ValueError("Unknown project record type")
        rows = self.query("SELECT * FROM type::thing($table, $key);", {"table": table, "key": record_id})[0]["result"]
        if not rows:
            return None
        row = rows[0]
        row.pop("id", None)
        return row

    def manifest(self):
        return self.get_record("manifest", "project")

    def initialize(self, project, canonical_revision):
        self.query("""
IF (SELECT * FROM ONLY manifest:project) = NONE {
    CREATE manifest:project CONTENT $manifest;
};
""", {"manifest": {"project_id": self.project_id, "project": project,
                        "canonical_revision": canonical_revision, "created_at": now(), "turns": 0}})

    def bundle(self, nodes=(), artifacts=(), links=(), decisions=()):
        # Immutable, content-addressed records. All referenced bytes are committed
        # before a run can claim them as inputs.
        blobs, metadata = [], []
        for artifact in artifacts:
            row = dict(artifact)
            blobs.append({"key": row["key"], "base64": row.pop("base64")})
            metadata.append(row)
        self.query("""
BEGIN TRANSACTION;
FOR $row IN $nodes {
    IF (SELECT * FROM ONLY type::thing('node', $row.key)) = NONE {
        CREATE type::thing('node', $row.key) CONTENT $row;
    };
};
FOR $row IN $artifacts {
    IF (SELECT * FROM ONLY type::thing('artifact', $row.key)) = NONE {
        CREATE type::thing('artifact', $row.key) CONTENT $row;
        CREATE type::thing('node', $row.key) CONTENT $row;
    };
};
FOR $row IN $blobs {
    IF (SELECT * FROM ONLY type::thing('blob', $row.key)) = NONE {
        CREATE type::thing('blob', $row.key) CONTENT $row;
    };
};
FOR $row IN $decisions {
    IF (SELECT * FROM ONLY type::thing('decision', $row.key)) = NONE {
        CREATE type::thing('decision', $row.key) CONTENT $row;
        CREATE type::thing('node', $row.key) CONTENT $row;
    };
};
FOR $edge IN $links {
    LET $from = type::thing('node', $edge.subject);
    LET $to = type::thing('node', $edge.object);
    LET $relation = type::thing('link', $edge.key);
    IF (SELECT * FROM ONLY $relation) = NONE {
        RELATE $from->$relation->$to CONTENT $edge;
    };
};
COMMIT TRANSACTION;
""", {"nodes": list(nodes), "artifacts": metadata, "blobs": blobs, "links": list(links), "decisions": list(decisions)})

    def read_artifact(self, artifact_id):
        artifact = self.get_record("artifact", artifact_id)
        blob = self.get_record("blob", artifact_id)
        if not artifact or not blob:
            raise KeyError("Project artifact not found")
        content = base64.b64decode(blob["base64"], validate=True)
        if hashlib.sha256(content).hexdigest() != artifact["sha256"]:
            raise ValueError("Artifact checksum mismatch")
        return artifact, content

    def list_records(self, table, offset=0, limit=100):
        if table not in {"node", "artifact", "run", "decision", "link"}:
            raise ValueError("Unknown project record type")
        return self.query("SELECT * FROM type::table($table) ORDER BY key LIMIT $limit START $offset;",
                          {"table": table, "limit": limit, "offset": offset})[0]["result"]

    def nodes_of_kind(self, kind, run_id=None, limit=200):
        if kind not in {"finding", "check_result"}:
            raise ValueError("Unknown project node kind")
        clause = "AND run_id = $run " if run_id else ""
        rows = self.query(f"SELECT * FROM node WHERE kind = $kind {clause}ORDER BY key LIMIT $limit;",
                          {"kind": kind, "run": run_id, "limit": limit})[0]["result"]
        for row in rows:
            row.pop("id", None)
        return rows

    def agent_run(self, job_id):
        if not re.fullmatch(r"[a-f0-9]{64}", job_id):
            raise ValueError("Expected workflow job ID")
        rows = self.query(
            "SELECT * FROM run WHERE job_id = $job ORDER BY started_at DESC LIMIT 1;",
            {"job": job_id},
        )[0]["result"]
        if not rows:
            return None
        row = rows[0]
        row.pop("id", None)
        row.pop("claim_token", None)
        return row

    def claim(self, run, token):
        self.query("""
BEGIN TRANSACTION;
LET $current = SELECT * FROM ONLY type::thing('run', $run.key);
IF $current.status = 'completed' { THROW 'Run already completed'; };
IF $current.status = 'running' AND <datetime>$current.lease_until > time::now() {
    THROW 'Run is already running';
};
IF $current = NONE { UPDATE manifest:project SET turns += 1; };
LET $manifest = SELECT * FROM ONLY manifest:project;
LET $turn = IF $current = NONE { $manifest.turns } ELSE { $current.turn };
UPSERT type::thing('run', $run.key) CONTENT $run;
UPDATE type::thing('run', $run.key) SET turn = $turn, claim_token = $lease_token,
    lease_until = <string>(time::now() + 20m), status = 'running';
COMMIT TRANSACTION;
""", {"run": run, "lease_token": token})
        return self.get_record("run", run["key"])

    def trace(self, run_id, token, phase, message, details=None, status="running"):
        event = {"at": now(), "phase": phase, "status": status, "message": message}
        if details:
            event["details"] = details
        self.query("""
BEGIN TRANSACTION;
LET $current = SELECT * FROM ONLY type::thing('run', $key);
IF $current.claim_token != $lease_token OR $current.status != 'running' { THROW 'Run lease lost'; };
UPDATE type::thing('run', $key) SET phase = $phase, updated_at = $at, trace += $event;
COMMIT TRANSACTION;
""", {"key": run_id, "lease_token": token, "phase": phase, "at": event["at"], "event": event})
        return event

    def finish(self, run_id, token, status, output):
        self.query("""
BEGIN TRANSACTION;
LET $current = SELECT * FROM ONLY type::thing('run', $key);
IF $current.claim_token != $lease_token OR $current.status != 'running' { THROW 'Run lease lost'; };
UPDATE type::thing('run', $key) SET status = $status, output = $output, finished_at = $finished;
COMMIT TRANSACTION;
""", {"key": run_id, "lease_token": token, "status": status, "output": output, "finished": now()})
        return self.get_record("run", run_id)


def artifact(filename, content, *, source_ids=(), derived_from=(), kind="artifact", role="evidence"):
    digest = hashlib.sha256(content).hexdigest()
    artifact_id = key(filename, digest, sorted(source_ids), sorted(derived_from), role)
    return {"key": artifact_id, "kind": kind, "filename": filename, "sha256": digest,
            "size_bytes": len(content), "base64": base64.b64encode(content).decode(),
            "source_ids": sorted(source_ids), "derived_from": sorted(derived_from), "role": role}


def link(subject, predicate, object_id):
    return {"key": key(subject, predicate, object_id), "subject": subject,
            "predicate": predicate, "object": object_id}


if __name__ == "__main__":
    # Explicit local bootstrap; production uses the VM startup template.
    password = os.environ["SURREAL_PROJECT_ADMIN_PASSWORD"]
    if not re.fullmatch(r"[A-Za-z0-9]{16,}", password):
        raise ValueError("Bootstrap requires an alphanumeric provisioning password of at least 16 characters")
    Store().query("DEFINE NAMESPACE IF NOT EXISTS projects; USE NS projects; "
                  "DEFINE USER OVERWRITE workflow_provisioner ON NAMESPACE PASSWORD '" + password + "' ROLES OWNER;")
    print("Project namespace and provisioning identity initialized")
