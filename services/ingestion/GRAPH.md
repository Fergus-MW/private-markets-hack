# Connector knowledge graph

Gmail and Google Drive → deterministic decoding/tables → existing Unstructured parser for complex files → optional Gemini Flash extraction → validated canonical graph in the existing SurrealDB.

The service exposes read-only Google connectors through `POST /connectors/sync`. It does not send email or change Drive files. This is application-side Google OAuth integration; Codex's connected-app session is not an OAuth credential available to the deployed service.

## Canonical schemas

`GET /graph/schema` returns the complete machine-readable schema. Every canonical entity has `key`, `kind`, `name`, `aliases`, namespaced `external_ids`, evidence `sources`, and an optional `merged_into` redirect. Additional fields are fixed:

| Entity | Fields | Automatic identity matching |
|---|---|---|
| Person | emails, contact_type | Normalized email or namespaced external ID |
| Company | registration_number, jurisdiction, lei, domains | LEI, jurisdiction + registration number, or namespaced external ID |
| Fund | lei, currency, domicile | LEI or namespaced external ID, e.g. `corvus:legal_entity` |
| Project | fund_id, management_company_id, quarter, workflow_type, status, completed_at, completion_source_id | Fund + management company + calendar quarter + workflow type |

Names and shared domains never auto-merge records. Unidentified names are scoped to their evidence source. Mail headers identify contacts; a team mailbox is not assumed to be a human, so `contact_type` starts as `unknown`. Conflicting identifiers fail the page rather than overwriting identity. `POST /graph/merge` explicitly merges compatible identities, preserves redirects and source evidence, and consolidates project scopes affected by a merge. Canonical names use first observed value; subsequent names become aliases.

A project is one administration workflow for one fund, one calendar quarter, and one management company. Work in progress is represented with `status=in_progress`; completed workflows require both a timestamp and an evidence source. Gemini can identify project scope but cannot declare completion. Use the typed entity endpoint to record evidenced completion. Administrator, GP, and management company are distinct roles; the mock LPA's GP is not automatically promoted to management company.

Source nodes retain connector account, external ID, revision, SHA-256, filename, text, metadata and ingestion timestamp. Email attachments have their own source nodes and `attached_to` edges. Source revisions remain separate even when names match. Identical attachment/file bytes retain separate provenance in each connector. Parsed complex documents also link to the existing `/documents/{id}/context` API. Structured sources keep their full text directly on the source node.

Edges include `sent`, `received`, `attached_to`, `mentions`, `works_for`, `manages`, `invests_in`, `for_fund`, `for_company`, `part_of`, and `received_via`. They retain evidence source and extraction method. There is no separate fact collection. Source documents and structured source rows retain the original values and provenance. `/graph/entities/{id}?as_of=2026-06-30` flattens identity redirects and relationships.

## Extraction

1. Gmail raw MIME: Python email parser reads headers, bodies and attachments, without a model. Gmail is fetched through `messages.list` and `messages.get(format=raw)` with pagination.
2. Drive: download bytes; export native Docs/Sheets/Slides as DOCX/XLSX/PPTX. The query is explicit. Folder queries cover direct children; traverse additional folders with separate requests. Folders and shortcuts are not imported as documents.
3. UTF-8 text/CSV/TSV and XLSX: direct decoding and tabular extraction. XLSX uses cached values and never recalculates formulas. Recognized tables use deterministic mappings. Unknown tables retain source text.
4. Other supported files, including PDF side-letter attachments: reuse the existing Unstructured/OCR and context pipeline. Unsupported or invalid attachments fail the page explicitly.
5. With `use_gemini=true`, remaining text (up to 60,000 characters per source) goes to `gemini-3.8-flash`, the latest Flash model verified on 5 September 2026. `GEMINI_MODEL` can override it with another Gemini Flash model. JSON output is schema validated; names, identifiers and relationship/project endpoints must be supported by exact source quotes. Accepted model output retains model version and quotes on its source. Names alone cannot merge across sources. Larger sources return a warning and remain available for narrower extraction; they are never silently truncated.

The agent has no tools or write privileges. It does not compute financial terms, invent identifiers, or declare project completion. Deterministic structured ingestion never calls Gemini. `use_gemini` defaults to false so fixture runs need no model credentials; enable it explicitly for live free-text entity extraction.

Supported structured mappings:

- Loader `entity_aliases.csv`: listing/source names share the explicit Corvus legal-entity ID.
- `entity_terms_v1.csv` and the equivalent XLSX: fund identity and currency; other values remain in the source table.
- Investor terms tables: require explicit canonical `fund_id`. Investor-in-vehicle IDs create investment-account **data nodes**, not global company identities. Source rows and dated investment edges preserve their vehicle scope. This avoids merging separate investor legal entities solely because of account IDs or names.
- Generic canonical CSV: `kind,name,id_namespace,external_id`, with optional `email` for people. Kinds are person, company or fund.

## Configuration and API

Configure the existing service with either a short-lived `GOOGLE_ACCESS_TOKEN`, or `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, and `GOOGLE_REFRESH_TOKEN`. Tokens need Gmail read-only and Drive read-only scopes (`https://www.googleapis.com/auth/gmail.readonly`, `https://www.googleapis.com/auth/drive.readonly`). Access tokens are refreshed once on 401 when a refresh token is available. Enable the Gmail and Drive APIs for the OAuth project. Set `GEMINI_API_KEY` when using model extraction. Keep credentials in deployment secrets; requests never carry OAuth tokens. The service continues using its existing Cloud Run IAM authorization boundary and single-workspace database.

Example request bodies for `POST /connectors/sync`:

```json
{"provider":"gmail","query":"label:fund-admin","page_size":10,"use_gemini":true}
```

```json
{"provider":"drive","query":"'FOLDER_ID' in parents","page_size":10,"use_gemini":true}
```

Follow `next_page_token` by submitting the same query/options and `page_token` until it is null. The response token is returned only after the whole page commits. Retry the same page after transient errors or concurrent-write failures. Query/token persistence and scheduling belong to the caller; this implementation does not install a background sync daemon or process Google deletion/change feeds.

For known workflow scope, add:

```json
{
  "provider":"gmail",
  "query":"label:fund-admin subject:Q3",
  "project_scope":{
    "fund_id":"CANONICAL_FUND_KEY",
    "management_company_id":"CANONICAL_COMPANY_KEY",
    "quarter":"2026-Q3",
    "workflow_type":"fee_run"
  },
  "use_gemini":true
}
```

This creates/reuses the quarterly project and links each source. The referenced canonical entities must already exist. With no scope supplied, Gemini can identify projects only where the full scope is explicitly supported by the source.

For structured terms, supply `fund_id` and the export's `snapshot_as_of`, e.g. `2026-06-30` for the baseline and `2026-07-01` for the amended table. The snapshot date determines which version of the export was applicable, separately from individual terms' `valid_from`. `GET /graph/funds/{fund_id}/terms?as_of=2026-09-30` returns original CSV-shaped `rows` and separate provenance. It selects the most recent applicable row validity date and snapshot; conflicting rows at the same rank return 409. It does not infer amendments from prose or implement the partner triple-delta protocol.

Other endpoints:

- `GET /graph/entities?kind=fund`, `GET /graph/sources`: paginated reads.
- `GET /graph/sources/{id}`: full evidence text and model quotes.
- `POST /graph/entities`: `{ "entity": { "kind":"company", "name":"Example", "external_ids":{"registry":"123"} }, "source_id":"EXISTING_SOURCE" }`; the same schema supports project scope and evidenced completion. `key` is generated server-side.
- `POST /graph/merge`: `{ "keep":"KEY", "duplicate":"KEY", "evidence_source_id":"SOURCE_KEY" }`.

## Persistence and verification

`kg_state` is a versioned write snapshot; `kg_node` and native `kg_link` relations are its graph projection. Each page commits atomically with optimistic concurrency. Stale snapshots fail; retries reload the graph. Existing document tables are preserved. Legacy snapshot `facts` fields are ignored on load and removed on the next successful graph save. The entire graph is loaded per request: this is suitable for the current prototype, and requires a partitioned/indexed write model before large mailbox ingestion.

```sh
PYTHONPATH=services/ingestion python -m unittest discover -s services/ingestion/tests -v
PYTHONPATH=services/ingestion python services/ingestion/scripts/verify_graph_examples.py \
  --loader-root /path/to/02-loader-gate \
  --terms-root /path/to/05-terms-and-side-letter-demo
```

The fixture verifier uses the installed parser for the real MIME/PDF attachment and programmatic CSV/XLSX extraction. It checks both complete terms snapshots against the original CSVs, including the Q2/Q3 fee change. It uses no live credentials and makes no database writes. Set `KG_DB_TESTS=1` when running unit tests against local SurrealDB to verify native traversal, replay and stale-write rejection in temporary tables.
