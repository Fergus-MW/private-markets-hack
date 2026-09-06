"""Versioned schema for the databases this service provisions.

`DEFINE ... IF NOT EXISTS` only has an effect the first time a database is
created, so a definition added later never reaches databases that already
exist. With a database per user and per project that is most of them. Each
applied migration is recorded in `schema_migration` inside its own database, and
whatever has not been applied runs on the next provision.

Every statement must stay idempotent. DDL and the record commit in one
transaction, but a migration that fails midway is retried from the start on the
next provision, so re-running it must be harmless.
"""
import os

from app.store import Store

# Per-user graph databases. kg_state holds the validated snapshot; kg_node and
# kg_link are its projection, written by UPSERT and RELATE.
GRAPH = [
    ("0001_graph_tables",
     "DEFINE TABLE IF NOT EXISTS kg_state SCHEMALESS;"
     "DEFINE TABLE IF NOT EXISTS kg_node SCHEMALESS;"
     "DEFINE TABLE IF NOT EXISTS kg_link SCHEMALESS;"),
]

# Per-project databases. The indexes back the only two filtered reads in the
# service; every other project read is a full table scan by design.
PROJECT = [
    ("0001_project_tables",
     "DEFINE TABLE IF NOT EXISTS manifest SCHEMALESS;"
     "DEFINE TABLE IF NOT EXISTS node SCHEMALESS;"
     "DEFINE TABLE IF NOT EXISTS link SCHEMALESS;"
     "DEFINE TABLE IF NOT EXISTS artifact SCHEMALESS;"
     "DEFINE TABLE IF NOT EXISTS decision SCHEMALESS;"
     "DEFINE TABLE IF NOT EXISTS run SCHEMALESS;"
     "DEFINE TABLE IF NOT EXISTS blob SCHEMALESS;"),
    ("0002_project_indexes",
     # SELECT * FROM node WHERE kind = $kind ORDER BY key
     "DEFINE INDEX IF NOT EXISTS node_kind ON node FIELDS kind;"
     # SELECT * FROM run WHERE job_id = $job ORDER BY started_at DESC
     "DEFINE INDEX IF NOT EXISTS run_job_id ON run FIELDS job_id;"),
]

RECORD = ("UPSERT type::thing('schema_migration', $name) "
          "CONTENT {name: $name, applied_at: time::now()};")


def steps(database):
    """Migrations for a database, chosen by the prefix provision_database validates."""
    return GRAPH if database.startswith("user_") else PROJECT


def admin_store(database):
    """Namespace-scoped principal: defining tables needs more than the runtime user."""
    return Store(namespace="projects", database=database,
                 user=os.environ["SURREAL_PROJECT_ADMIN_USER"],
                 password=os.environ["SURREAL_PROJECT_ADMIN_PASSWORD"],
                 auth_level=os.environ.get("SURREAL_PROJECT_ADMIN_AUTH_LEVEL", "namespace"))


def apply(database, store=None):
    """Apply outstanding migrations. Returns the names applied, in order."""
    store = store or admin_store(database)
    # A database with no schema_migration table reads as empty rather than failing.
    rows = store.query("SELECT name FROM schema_migration;")[0]["result"]
    applied = {row["name"] for row in rows}
    ran = []
    for name, statements in steps(database):
        if name in applied:
            continue
        store.query("BEGIN TRANSACTION;" + statements + RECORD + "COMMIT TRANSACTION;", {"name": name})
        ran.append(name)
    return ran
