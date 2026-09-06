# Multi-user knowledge graphs

Each connected Google account owns a private canonical graph. Its tenant ID is the existing frontend connector ID derived from the verified account email. No team sharing or cross-user merge is enabled.

The namespace `projects` contains:

- `user_<sha256(tenant)>`: that user's canonical entities, sources, source bytes, parsed documents and graph edges.
- `project_<sha256(tenant + ':' + project_id)>`: one independent workflow graph for that user's project, including originals, intermediate files, decisions and runs.

Canonical IDs remain stable. Identical source/entity/project keys in two users' graphs refer to separate database records. Runtime credentials are derived independently per database. Only the internal provisioner can create databases; user-facing queries use database-scoped credentials. No facts are stored.

## Identity and access

Production Terraform sets `GRAPH_MULTI_USER=true`. Requests require a short-lived `X-Graph-Identity` assertion signed by the trusted frontend or connector service, in addition to Cloud Run IAM authentication. Assertions bind tenant, actor, method, path and expiry. An arbitrary user/account field, header, URL or project ID cannot select another user's database. Health and format discovery return no user data and need no assertion.

The frontend validates its encrypted, expiring connection cookie and proxies `/api/graph`, `/api/projects`, `/api/sources` and `/api/documents` to ingestion. It discards client identity/authorization headers, signs the session owner, and uses its own Cloud Run identity. Writes require the same Origin as the configured frontend. Ratification actor attribution comes from this signed identity, overriding caller-supplied actor names.

Connector workers derive the tenant from the per-account OAuth secret name. A conflicting archive prefix fails before ingestion. Both Gmail and Drive for one account populate the same canonical graph. A legacy shared service connection is assigned a separate deterministic service tenant, never a signed-in user's graph. Connector assertions permit only POST `/sources`. The old global-credential `/connectors/sync` endpoint is disabled for user-scoped requests.

Signing keys are held only by trusted server processes. Browser clients never receive them. Those trusted services remain part of the security boundary and can sign identities; database isolation does not defend against compromise of the provisioning service.

## Existing data and local operation

The legacy `markets/documents` canonical database and old unscoped project databases are preserved. They are not automatically assigned to the first user who signs in. Re-ingest through the account-scoped worker to populate that account's graph, or perform an explicit ownership-reviewed migration. New completion-marker versions reprocess earlier document-only ingestions once.

Local development without `GRAPH_MULTI_USER=true` retains the legacy single-workspace API. Production must keep the flag enabled. Users sign in through the existing frontend; the graph API is available through the authenticated proxy. This change adds API access and isolation, not a graph visualization UI, team invitations or shared-workspace roles.

## Deployment

Apply Terraform first: it creates the signing secret and access grants and runs the tracked project namespace bootstrap through IAP. Then build/deploy the updated ingestion service, connector worker and frontend using the existing Cloud Build configurations (set `_JOBS` for connectors and `_SERVICES=frontend` for the frontend). Configure `google_connectors`, `connector_image`, `frontend_image`, `frontend_public_origin` and `frontend_oauth_client_id`; empty/default values deliberately omit those services. Import pre-existing out-of-state resources before applying. Terraform intentionally leaves image releases to Cloud Build. The apply operator needs Python 3, authenticated gcloud, IAP tunnel access and OS Login access on the VM. The bootstrap obtains pinned secret versions using the VM identity and never reads production secret values on the operator's machine. A failed migration blocks the application resource update; rerun the apply after fixing access or startup readiness.

The migration tracks VM identity, script content and secret versions; it does not replace or restart the existing database VM. The project credential derivation key must remain stable for existing databases; rotating it requires a deliberate credential migration.

Validation covers unsigned/forged/expired/wrong-route identities, session reset, frontend CSRF checks, connector credential/prefix matching, identical keys in different users' canonical/project databases, separate original bytes, API project ownership, and rejected cross-database authentication.
