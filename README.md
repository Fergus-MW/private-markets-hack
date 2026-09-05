# Private markets document context

The [ingestion service](services/ingestion/) turns professional-services documents into source-grounded context for agents using **Unstructured**. It runs on the existing authenticated Cloud Run service and stores parsed documents in the existing private SurrealDB instance. Terraform lives in [`infrastructure/`](infrastructure/).

```
Upload → Unstructured partitioning / OCR → section-aware chunks → SurrealDB
                                                                ↓
                                            agent context + source citations
```

There is no person/company extraction, entity resolution, or spaCy model. The service prepares source material; it does not summarize, generate embeddings, or answer questions.

## Input formats

Gmail and Google Drive sources can also be pulled by dedicated Cloud Run Jobs. They archive original files and Google-native document exports in private GCS buckets, then pass supported documents, spreadsheets, presentations, emails and images to this service. Google Docs, Sheets and Slides export to DOCX, XLSX and PPTX. See [connector provisioning and account setup](infrastructure/CONNECTORS.md) for full-mailbox imports, Drive files, schedules, and retry behavior.

- Reports and contracts: PDF, DOC, DOCX, ODT, RTF.
- Presentations: PPT, PPTX.
- Financial data: XLS, XLSX, CSV, TSV.
- Correspondence: EML, MSG (email bodies and available headers).
- Web/text: HTML, TXT, Markdown, RST, XML, EPUB.
- Scans: PNG, JPEG, TIFF, BMP, HEIC, and image-only PDFs with English OCR.

`GET /formats` returns the exact extension allowlist. Legacy Office formats use LibreOffice; document conversions use Pandoc. An extension being supported does not guarantee every encrypted, corrupt, or unusual variant can be parsed. Audio, video, generic ZIP archives, and recursive email attachment ingestion are not supported. Upload attachments as separate documents.

Uploads are limited to 20 MiB, expanded Office/EPUB containers to 100 MiB, and extracted text to one million characters. Requests are synchronous, with the existing 15-minute cloud timeout. Retry the same document after a timeout or 503.

For PDFs, the default `pdf_strategy=auto` extracts embedded text and falls back to OCR when no text is found. Choose `ocr_only` for mixed text/scanned PDFs, or `hi_res` for layout-sensitive extraction such as PDF tables. High-resolution parsing downloads its layout model on first use and can take longer. Spreadsheet formulas are not recalculated; extraction uses stored workbook content. Table HTML is preserved when the parser provides it.

## Agent API

```sh
docker compose up --build -d
curl -f -F 'file=@report.pdf' http://localhost:18080/documents
# For a scanned or layout-sensitive PDF:
curl -f -F 'file=@report.pdf' 'http://localhost:18080/documents?pdf_strategy=ocr_only'
```

Upload returns `document_id`, element/chunk counts, parser warnings, and `context_url`.

```sh
curl -f http://localhost:18080/documents/DOCUMENT_ID
curl -f 'http://localhost:18080/documents/DOCUMENT_ID/context?max_characters=20000&limit=10'
curl -f 'http://localhost:18080/documents/DOCUMENT_ID/elements?offset=0&limit=20'
```

The context endpoint returns ordered chunks with stable IDs, text, original filename, source element IDs, and available page/slide numbers or spreadsheet sheet names. Follow `next_offset` until it is null. The text budget is measured in characters, not tokens; JSON metadata adds overhead. Each chunk contains at most 4,000 characters, and the request budget must be at least 4,000. Unstructured title chunking respects detected section and page boundaries, keeps tables separate, and uses 200-character overlap when splitting oversized elements.

The elements endpoint exposes the underlying typed source elements and metadata, including table HTML and links when available. Agents can use the chunk citations to look up the source elements; document text should be treated as source material, not trusted instructions. The API does not provide cross-document semantic search or a vector index.

The document SHA-256 identifies the original bytes. Reuploading replaces the whole document's parsed elements and context atomically. Chunk IDs are stable for the same document, pipeline version, index, and text; changed chunk text produces a different ID. Original binary files are discarded after parsing; retain source files separately. Available source metadata is retained, but unavailable page or sheet references are never invented.

Existing legacy document records must be reuploaded before they have context; the API returns 409 for those records. Historical person/company/mention tables are not read or written by this service and are retained rather than deleted during the repurpose.

## Deployment and verification

The same Cloud Run service, private database VM, 10 GB data disk, backups, network, and identities are reused. Cloud calls require `Authorization: Bearer $(gcloud auth print-identity-token)`. See [deployment](infrastructure/DEPLOYMENT.md) and [Cloud Build](infrastructure/CLOUD_BUILD.md) for the deployed endpoint and pipeline setup.

```sh
PYTHONPATH=services/ingestion python3 -m unittest discover -s services/ingestion/tests -v
docker compose cp services/ingestion/tests/smoke.py ingestion:/tmp/smoke.py
docker compose exec -e PYTHONPATH=/app ingestion python /tmp/smoke.py
```

Local smoke tests exercise text, Word, PowerPoint, Excel, CSV/TSV, Markdown, HTML, email, scanned PDF and image parsing; source citations; pagination; duplicate ingestion; rejected inputs; and database-scoped access. Local ports are loopback-only: ingestion `18080`, SurrealDB `18000`. `docker compose down` stops containers and retains the database volume.

Reference: [Unstructured partitioning](https://docs.unstructured.io/open-source/core-functionality/partitioning), [Unstructured chunking](https://docs.unstructured.io/open-source/core-functionality/chunking).
