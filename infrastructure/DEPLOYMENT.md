# Deployment

- Display name: **private markets hack**
- Project ID: `private-markets-hack`
- Project number: `230147580347`
- Organisation parent: none (matching the supplied `grenertia` project)
- Billing: same active billing account as `grenertia`
- Region / zone: `europe-west2` / `europe-west2-a`
- Project console: https://console.cloud.google.com/home/dashboard?project=private-markets-hack
- Ingestion service: https://document-ingestion-gucopvqxoq-nw.a.run.app
- SurrealDB private IP: `10.42.0.2:8000` (no public IP)
- Data disk: 10 GB; separate boot disk: 10 GB; daily snapshots
- Cloud Run invoker: `gangevo@gmail.com` (Google's canonical spelling of `gangevo@googlemail.com`)

## Document context service

The existing service has been repurposed from entity extraction to Unstructured document parsing and agent context. Revision `document-ingestion-00003-fjt` is live. The same database, disk, network, runtime identity, and Cloud Run service are reused. The pipeline no longer extracts people or companies.

The API accepts common reports, contracts, presentations, spreadsheets, emails, web/text files, and scanned documents. It stores typed elements and section-aware chunks, with stable citations, source pages/slides/sheets, and table HTML where available. See the [API and format guide](../README.md).

- `POST /documents`: ingest a file and return its document ID and context URL.
- `GET /documents/{id}/context`: retrieve context within a character budget.
- `GET /documents/{id}/elements`: retrieve source elements and table metadata.
- `GET /documents/{id}`: retrieve processing metadata.
- `GET /formats`: list supported file extensions and limits.

Legacy documents must be reuploaded to gain context. Historical entity tables are retained but are no longer used. Original file bytes are not retained; preserve source documents separately.

## Verification

Local integration checks passed for TXT, DOCX, PPTX, XLSX, CSV, TSV, Markdown, HTML, EML, scanned PDF/PNG, DOC, XLS, PPT, RTF, ODT, EPUB, XML, and RST. High-resolution PDF table extraction also passed. Context tests covered source citations, chunk size limits, pagination, repeat ingestion, and database-scoped writes.

Cloud deployment build: [33083c57-7b88-4f35-a79a-1bccad8a90ca](https://console.cloud.google.com/cloud-build/builds;region=europe-west2/33083c57-7b88-4f35-a79a-1bccad8a90ca?project=230147580347). The build succeeded. Live checks passed for IAM authentication, database readiness, document ingestion, agent context retrieval with source citations, and duplicate handling. Upload responses contain chunk counts and context URLs, with no person/company extraction fields.

Run `python3 services/ingestion/tests/cloud_smoke.py` from the repository root with your authorized GCP login for the live upload/context/retry check. It retains a synthetic test document.

Local Terraform state and deployment variables are gitignored; preserve the state securely. Cloud Build owns application image updates. Automatic GitHub triggering still awaits the separate connection authorization described in [CLOUD_BUILD.md](CLOUD_BUILD.md).
