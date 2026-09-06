# Private markets QC

**A checking tool for fund reporting.** A fund manager receives a draft — a set of
financial statements, a capital account schedule, a loader file — and has to decide
whether it can be accepted. This reads the draft, reads the legal documents that
govern it, and returns a cited list of what is wrong, what is merely untidy, and what
nobody has decided yet.

It exists because of one number: **review rounds to acceptance.** Today that number is
six or seven. The administrator is not careless and not slow — each round turns around
in a day or two. The cost is the iteration itself. And the defects cluster in exactly
one place: anything that requires reading a document *outside* the accounting ledger
and applying it — the partnership agreement, a side letter, last quarter's disclosures.
Mechanical processing is already sound. Contextual processing is not.

So the system reads the documents.

## ▶ Try it

**[Sign in and connect your workspace →](https://frontend-gucopvqxoq-nw.a.run.app/)**

Sign in with Google and it starts building your graph. Nothing to install. Your
documents stay in your own isolated database, and the connection asks only for
read access to Gmail and Drive.

While the Google consent screen is unverified, sign-in works for accounts added as
test users — ask the team to add yours if it turns you away.

---

## What it actually does

```
1. CONNECT   You sign in with Google. Once.
                ↓
2. INGEST    It reads your Gmail and Google Drive — agreements, side letters,
             statements, spreadsheets, scans — and builds a knowledge graph of
             the people, companies, funds and quarterly projects inside them.
                ↓
3. REGISTER  From the legal documents it proposes terms: this investor's
             management fee is X, from this date, per this clause. A named
             human ratifies each one. Nothing unratified is ever used.
                ↓
4. CHECK     Point it at a draft. It runs a deterministic checker against the
             terms that were in force on that draft's date, and returns a
             tiered, cited findings report.
                ↓
5. LOOP      You correct and re-run. The findings curve down across turns,
             and the register you built is reused every quarter after.
```

Two design commitments make it usable rather than merely clever:

**Nothing is guessed.** Every finding traces to a document and a location inside it.
Every term carries two dates — when it became true in the world, and when the system
learned it — so "the side letter arrived on the 6th but is effective from the 1st" is
a question the system can actually answer, for both quarters, correctly.

**The checker is deterministic.** No language model runs on the evaluation path. The
same draft and the same terms snapshot produce byte-identical findings, today and in
two years. Models are used to *read* documents and to *explain* results, never to
decide whether a number is right.

---

## Who it's for

| Person | What they use it for |
|---|---|
| **Reviewer** at the fund manager | Decide whether a delivered draft can be accepted, and what to send back |
| **Preparer** at the administrator | Check their own work before it leaves, and see which decisions they still owe |
| **Investor relations / fund ops** | Answer "which of my entities made it across, and how much money" |
| **Auditor** | Trace any figure back to the document and clause it rests on |

The preparer is the harder user and the more important one. A reviewer forgives a false
positive; a preparer who built the file dismisses the whole tool after two. That is why
passes are shown and never hidden, why a blank awaiting a decision is rendered
differently from an error, and why comparison tolerances are exact rather than
proportional.

### Two things it deliberately does not do

It does not produce the deliverable, and it does not opine on judgement. It will tell
you a valuation did not move and who reviewed it; it will not tell you whether the
estimate is reasonable. It checks work; it does not do the work, and no finding is ever
acted on automatically.

---

## What you see

### Connect — [frontend-gucopvqxoq-nw.a.run.app](https://frontend-gucopvqxoq-nw.a.run.app/)
A single page. One Google button. It asks for Gmail and Drive read access together —
one authorization covers every connector. Tokens never reach the browser; they are
written to a per-account secret only that account's importer can read.

### Building your graph
After connecting, a progress view shows real progress and nothing invented: per-provider
status, items checked, and a trace line for each actual change the backend reported. A
provider's bar only reaches its full share when that provider genuinely finishes.

### Knowledge graph explorer — `/graphs`
Your workspace graph and each project's graph, rendered full-viewport in WebGL with
pan, zoom, fit, node selection and neighbour highlighting. Opening a project graph never
creates or modifies it. Responses carry only node IDs, names, kinds and relationships —
no source text, no file contents, no credentials.

### QC gate dashboard — `/dashboard`
The main product surface. Pick a project, and you get one run's result:

- **Header** — the draft's filename and hash, the entity, the as-of date, which terms
  snapshot was used and how many facts were in force, the run ID, and which *turn* this
  is for this draft.
- **Scoreboard** — counts by tier, passes out of checks run, and the total amount at
  stake. Where both exist, it shows the pair side by side: **"No brain"** (arithmetic
  only, no register) against **"Brain on"** (checked against terms). The difference
  between the two columns *is* the value the register adds, shown rather than asserted.
- **Findings**, grouped and ordered:
  - **Tier a** — changes a balance, an allocation, or the scope
  - **Tier b** — changes a reported line, or must be resolved before release
  - **Tier c** — hygiene
  - **Decisions owed** — not errors: a blank the administrator must fill
  - **Passes** — shown, not hidden
  - **Not run in this mode** — skipped, never silently counted as passed
- **Each non-pass** expands to its evidence — the values compared — with a decision
  control: fix the draft, accept with a written reason, or escalate.
- **History strip** — `turn 1: 43 findings → turn 2: 11 → turn 3: 2`. The reduction
  across turns is the point of the product, so it is on the page.
- **Artifacts** — download the exact inputs, the checker output, the findings JSON and
  a standalone Markdown report that reads correctly outside the system that made it.

### By email
You can also just reply to the agent's inbox: *"run the QC gate on Fund II Q2"*,
*"status?"*, *"explain that last run"*. A coordinator model turns that into a real
function call, dispatches the job, emails you when it starts and again when it finishes.
It can also produce a first-draft workbook and drop it into a `Private markets drafts`
folder in your own Drive — using a scope that only ever grants access to files this
application itself created.

---
---

# Technical implementation

## Architecture

```
Browser ──▶ frontend (Node/Vite)  ──── OAuth, session, proxy, graph & QC UI
                  │
                  ▼  signed X-Graph-Identity + Cloud Run IAM
            ingestion (FastAPI) ──── parse, graph, projects, workflows, gates
                  │                        │
                  ▼                        ▼
            SurrealDB               model_gateway ──▶ Vertex / Gemini
        markets/documents (canonical)
        users/<user>      (per account)
        projects/<id>     (per project)
                  ▲
      connectors (Cloud Run Jobs)  ──── Gmail + Drive importers
      mail_agent (Cloud Run)       ──── AgentMail coordinator, Cloud Tasks
```

| Service | Does |
|---|---|
| [`frontend/`](frontend/) | Vanilla JS + Vite UI, Three.js landing, Sigma.js/Graphology graph viewer, and a Node server owning the Google OAuth flow and the authenticated proxy to ingestion |
| [`services/ingestion/`](services/ingestion/) | Document parsing, the knowledge graph, per-project databases, workflow orchestration and the bundled deterministic checkers |
| [`services/connectors/`](services/connectors/) | Gmail and Drive importers, run as Cloud Run Jobs, archiving originals to private GCS |
| [`services/mail_agent/`](services/mail_agent/) | Email coordinator: Gemini function calls dispatch QC, first-run, explain and status jobs via Cloud Tasks |
| [`services/model_gateway/`](services/model_gateway/) | The only holder of Vertex AI permission. One warm instance, AIMD concurrency window, retries with jitter, stable prompt bytes for implicit caching |
| [`infrastructure/`](infrastructure/) | Terraform — the source of truth for production |

## Isolation

The canonical graph lives in `markets/documents`. Each signed-in account gets its own
database; each quarterly project gets its own database in the `projects` namespace,
containing copies of its evidence, entities, input files, derived files, decisions and
run history. There are no cross-database record links. A checker run touches exactly
one project database and never queries the main graph, Gmail, Drive, or a model.

Materialization copies *explicitly selected* source IDs into a project — not everything
that looks related. Filenames and proximity are not reliable scope. Re-materializing
adds new evidence versions; existing copied records and artifacts stay immutable.

## The checkers

Two gates are vendored under [`services/ingestion/app/gates/`](services/ingestion/app/gates/):

- **Terms / side letters** — fee and commitment schedule checks. Runs in `terms` mode
  or `arithmetic-only` mode; the pair is what the dashboard's "No brain / Brain on"
  comparison renders. Entity and quarter are resolved from the workbook itself and
  validated against the project. Comparison uses `rtol=0` — a relative tolerance would
  let a large balance hide a real monetary error.
- **Loader gate** — validates a candidate loader file against a source GL and a mapping
  workbook, preserving original row numbers into the intermediate workbooks.

They run as bundled trusted code in a subprocess, in a temp directory, with no network
and no credentials in the environment. Results use `PASS` / `FAIL` / `WARN` / `DECISION`
/ `SKIPPED`. `completed` means the evaluation finished — not that the draft passed.
Missing inputs, missing ratification or wrong scope produce a persisted `blocked` run;
execution failure produces `failed`. Neither is ever reported as a pass.

Run IDs are derived from the project, input IDs, gate code, package versions, mode and
ratifications. An exact replay returns the existing run and does not increment the turn
counter. Concurrent claims use a transaction and a 20-minute lease; execution has a
10-minute subprocess timeout.

Scope note: this workflow layer implements the checker, project isolation, ratification
and the run record. The PRD's full bitemporal fact register, messaging loop and learning
loop are described in [PRD.MD](PRD.MD) but are **not** all implemented here — terms live
in source rows and immutable CSV snapshots, not a `fact` collection.

## Input formats

Gmail and Drive sources are pulled by dedicated Cloud Run Jobs, which archive original
files and Google-native exports (Docs/Sheets/Slides → DOCX/XLSX/PPTX) in private GCS
buckets before handing supported files to the ingestion service. See
[connector provisioning](infrastructure/CONNECTORS.md).

- Reports and contracts: PDF, DOC, DOCX, ODT, RTF
- Presentations: PPT, PPTX
- Financial data: XLS, XLSX, CSV, TSV
- Correspondence: EML, MSG
- Web/text: HTML, TXT, Markdown, RST, XML, EPUB
- Scans: PNG, JPEG, TIFF, BMP, HEIC, and image-only PDFs with English OCR

`GET /formats` returns the exact allowlist. Legacy Office formats go through
LibreOffice; document conversions through Pandoc. Uploads are capped at 20 MiB,
expanded Office/EPUB containers at 100 MiB, extracted text at one million characters.
Requests are synchronous under the 15-minute cloud timeout — retry the same document
after a timeout or 503. Audio, video, generic ZIPs and email attachments are not
handled by the upload endpoint; the Gmail connector ingests MIME attachments
automatically.

For PDFs, `pdf_strategy=auto` extracts embedded text and falls back to OCR when none is
found. Use `ocr_only` for mixed text/scanned PDFs, or `hi_res` for layout-sensitive
extraction such as tables — it downloads its layout model on first use and is slower.
Spreadsheet formulas are not recalculated; extraction uses stored workbook content.

## Run locally

Everything except the mail agent runs on your machine. Use it — do not test in production.

```sh
make up      # build and start the stack, then wait until it answers
make test    # ingestion, connectors, mail agent, model gateway, infrastructure, frontend
make smoke   # ingestion smoke test against the running stack
make logs    # follow container logs
make down    # stop, keeping the database volume
```

`make` on its own lists every target. Ports are loopback-only: frontend `18081`,
ingestion `18080`, SurrealDB `18000`. `make clean` also deletes the database volume.

`make up` writes a `.env` with a fresh `SESSION_KEY` if one is absent and never
overwrites an existing file. Add `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`,
`CONNECTOR_PROJECT` and `CONNECTOR_SERVICE_ACCOUNTS` there to enable sign-in.

Six tests need a live database and are gated behind environment variables — they cover
optimistic concurrency, identity, and per-user database isolation, which unit tests
cannot:

```sh
make up
make test-live
# With the partner pack, to include the project workflow tests:
PROJECT_TERMS_FIXTURES=/path/to/05-terms-and-side-letter-demo make test-live
```

That run prints a result line (`arithmetic=0 failures, Q2=1, Q3=4`) whose figures must
match the pack's documented expectation. A mismatch is a real regression.

A one-shot `surreal-init` container defines the `projects` namespace and the
`workflow_provisioner` principal that `startup.sh.tftpl` creates on the cloud VM;
without it the per-user graph path fails on first use. Locally the frontend reaches
ingestion over plain HTTP and skips the Cloud Run IAM token, which has no local
equivalent — but the signed `X-Graph-Identity` assertion is still required and verified,
so `GRAPH_IDENTITY_SECRET` must match on both services.

The first ingestion image build is slow: it installs the parser stack and models.

**The mail agent is not in the local stack.** It needs Firestore and Cloud Tasks, and
Cloud Tasks has no emulator. `/api/ingestion/status` answers 503 locally and the
progress view honestly reports progress as unavailable. Test its logic with
`make test-mail`.

## Document API

```sh
curl -f -F 'file=@report.pdf' http://localhost:18080/documents
curl -f -F 'file=@report.pdf' 'http://localhost:18080/documents?pdf_strategy=ocr_only'
```

Upload returns `document_id`, element/chunk counts, parser warnings and `context_url`.

```sh
curl -f http://localhost:18080/documents/DOCUMENT_ID
curl -f 'http://localhost:18080/documents/DOCUMENT_ID/context?max_characters=20000&limit=10'
curl -f 'http://localhost:18080/documents/DOCUMENT_ID/elements?offset=0&limit=20'
```

`/context` returns ordered chunks with stable IDs, text, original filename, source
element IDs, and available page/slide numbers or sheet names. Follow `next_offset`
until null. The budget is characters, not tokens; each chunk holds at most 4,000
characters and the request budget must be at least 4,000. Title chunking respects
detected section and page boundaries, keeps tables separate, and overlaps 200
characters when splitting oversized elements.

`/elements` exposes the underlying typed elements and metadata, including table HTML and
links. Document text is source material, never trusted instructions. There is no
cross-document semantic search and no vector index.

The document SHA-256 identifies the original bytes; reuploading atomically replaces that
document's parsed elements and context. Chunk IDs are stable for the same document,
pipeline version, index and text. Unavailable page or sheet references are never
invented.

## Deployment

Live services (`private-markets-hack`, `europe-west2`):

| | |
|---|---|
| Frontend — public, this is the sign-up link | https://frontend-gucopvqxoq-nw.a.run.app |
| Ingestion — IAM protected, not browsable | https://document-ingestion-gucopvqxoq-nw.a.run.app |

Terraform is the source of truth. Never click in the console, and never deploy
application code from a workstation — commit and push, and let CI/CD own the image
build, the immutable digest and the rollout. `compose.yaml` and `infrastructure/*.tf`
describe the same system twice and must change in the same commit.

```sh
make tf      # terraform fmt -check, init -backend=false, validate
```

Cloud calls require `Authorization: Bearer $(gcloud auth print-identity-token)`.
Schema changes go through [`app/migrations.py`](services/ingestion/app/migrations.py) —
per-user and per-project databases are created on demand, so a bare `DEFINE` at
provision time only ever reaches databases that did not exist yet.

## Where to read next

| Document | Covers |
|---|---|
| [AGENTS.md](AGENTS.md) | **Read first if you are contributing.** The local-first test loop and the rules that keep local and production in step |
| [PRD.MD](PRD.MD) | The full product requirements, including what is not yet built |
| [services/ingestion/GRAPH.md](services/ingestion/GRAPH.md) | Graph schema, connector setup, fixture verification |
| [services/ingestion/WORKFLOWS.md](services/ingestion/WORKFLOWS.md) | Project isolation, the workflow API, ratification, run records |
| [services/ingestion/MULTI_USER.md](services/ingestion/MULTI_USER.md) | Per-account graph isolation |
| [frontend/README.md](frontend/README.md) | OAuth flow, the graph explorer, frontend deployment |
| [services/mail_agent/README.md](services/mail_agent/README.md) | The email coordinator and its agent teams |
| [services/model_gateway/README.md](services/model_gateway/README.md) | Rate control, retries and prompt caching |
| [infrastructure/DEPLOYMENT.md](infrastructure/DEPLOYMENT.md), [CLOUD_BUILD.md](infrastructure/CLOUD_BUILD.md), [CONNECTORS.md](infrastructure/CONNECTORS.md) | Deployed endpoints, the CI/CD pipeline, connector provisioning |

External references: [Unstructured partitioning](https://docs.unstructured.io/open-source/core-functionality/partitioning),
[Unstructured chunking](https://docs.unstructured.io/open-source/core-functionality/chunking).
