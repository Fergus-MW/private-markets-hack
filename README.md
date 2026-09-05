# Private markets document pipeline

Terraform lives in [`infrastructure/`](infrastructure/). The separate [ingestion service](services/ingestion/) accepts documents, parses them with Unstructured, extracts named people and organisations with spaCy, and writes documents, canonical name candidates, and source mentions into SurrealDB in one transaction.

```
Authenticated upload → Cloud Run ingestion → Unstructured → English NER
                                                ↓
                          private SurrealDB VM + 10 GB data disk
```

## Run locally

```sh
docker compose up --build -d
curl -f http://localhost:18080/readyz
curl -f -F 'file=@report.pdf' http://localhost:18080/documents
```

Local ports are loopback-only: ingestion `18080`, SurrealDB `18000`. Compose uses development-only root credentials; cloud uses a database-scoped editor. First startup can take several minutes to build the parser image and load the model. Stop with `docker compose down`; the database volume remains.

Supported inputs: PDF (including English OCR fallback), DOCX, TXT, HTML, and Markdown. Maximum file size is 20 MiB and maximum extracted text is one million characters. Requests run synchronously with a 15-minute cloud timeout. On timeout or a 503, retry the same file. There is no background queue in this initial version.

## Data and identity semantics

- `document`: SHA-256 content ID, filename, size, parsed elements, page numbers, pipeline version, completion time.
- `person`: normalized name candidates scoped to a document. Two people with identical names in different documents are not automatically merged.
- `company`: normalized organisation-name candidates across documents. spaCy's `ORG` label includes organisations other than legal companies; these are explicitly unverified.
- `mention`: links to document and entity records, original spelling, element index, page number if available, and character offsets within the element.

Canonicalization uses Unicode NFKC, case folding, and whitespace normalization. It preserves punctuation and corporate suffixes, and does not guess that acronyms, subsidiaries, or similar names are the same entity. Cross-document person identity resolution and verified company enrichment require additional identifiers or review; this initial pipeline does not claim to solve them. English statistical NER can miss or misclassify names. PDF parsing first extracts embedded text; if none is found it retries with OCR. Mixed PDFs containing both text pages and image-only pages may need an OCR-first mode in a future extension.

Reingesting identical bytes upserts the same document and replaces its mentions atomically. Entity IDs are deterministic, and aliases accumulate. Previous entity candidates may remain after a pipeline/model change, even if they no longer have mentions. The latest ingestion replaces the filename and parsed output. Original binary files are discarded after parsing; retain your source documents separately. Extracted text is retained in SurrealDB.

## Deploy to GCP

See [`infrastructure/README.md`](infrastructure/README.md). Defaults: London, an `e2-small` database VM, a **10 GB data disk** plus a separate 10 GB boot disk, daily snapshots retained for seven days, and Cloud Run with 2 CPUs / 4 GiB memory, concurrency one, zero minimum instances, and at most two instances.

The database has no public IP. Cloud Run requires IAM authentication and reaches it through direct VPC egress. VM outbound access uses Cloud NAT. This is a single-node deployment with maintenance downtime, not high availability. VM, disks, snapshots, NAT, registry, and Cloud Run usage incur GCP charges. The 10 GB data capacity excludes the OS disk and backups. Database traffic stays inside the VPC but uses HTTP, not application-level TLS.

## Verify

```sh
terraform -chdir=infrastructure init -backend=false
terraform -chdir=infrastructure validate
PYTHONPATH=services/ingestion python3 -m unittest discover -s services/ingestion/tests -v
docker compose cp services/ingestion/tests/smoke.py ingestion:/tmp/smoke.py
docker compose exec -e PYTHONPATH=/app ingestion python /tmp/smoke.py
```

The smoke test is for the local development database: it uploads fixtures, checks duplicate handling and source links, validates rejected inputs, verifies transaction rollback, and creates a test database-scoped editor.

Implementation references: [Unstructured partitioning](https://docs.unstructured.io/open-source/core-functionality/partitioning), [spaCy NER](https://spacy.io/usage/spacy-101), [SurrealDB RPC](https://surrealdb.com/docs/surrealdb/integration/rpc), and [Terraform Google provider](https://registry.terraform.io/providers/hashicorp/google/latest/docs).
