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

## The experience, start to finish

### 1. You connect — one button, once

[**frontend-gucopvqxoq-nw.a.run.app**](https://frontend-gucopvqxoq-nw.a.run.app/) is a
single page with a single Google button. It asks for Gmail and Drive **read** access
together, in one consent screen, because one authorization covers every connector — you
are never sent back to Google a second time to add another integration. Partial consent
is rejected: you either grant both or nothing is stored.

Your tokens never touch the browser. They are written to a secret whose name is derived
from your verified email address, readable only by the importer that runs on your
behalf. There is no shared slot, so one person's connection cannot overwrite or reach
another's.

The moment the connection lands, two things happen at once:

- an **ingestion run** is queued for your account, and
- a **welcome email** is queued from your agent.

### 2. You watch your graph get built

You are dropped straight onto a live progress view — you don't have to go looking for it.
It shows:

- a **progress bar** that is honest. A provider only reaches its full share of the bar
  when it has genuinely finished; while running it asymptotically approaches that share
  and never touches it. The bar shows 100% only when the state is actually `completed`.
- a **per-provider row** for Google Drive and Gmail — status, items checked, and a
  breakdown of what happened to them: ingested, already up to date, archived, metadata
  only, shortcuts, failed.
- an **AGENT ACTIVITY log**, timestamped, one line per real change the backend reported.
  No invented steps, no fake "analysing…" filler.

When the graph is ready it says so and takes you to `/graphs`. When it finishes *without*
a ready graph — a connector failed, or there were no supported files — it says that
plainly and offers the partial view instead of rounding up to success.

### 3. You explore what it found

`/graphs` lists your workspace graph plus a graph for each project. Opening one gives a
full-viewport WebGL view — pan, zoom, fit, click a node to select it and highlight its
neighbours — of the people, companies, funds, documents and quarterly projects it
extracted, and the links between them.

Opening a project graph never creates or modifies it. The data behind the view carries
only node IDs, names, kinds and relationship metadata — no source text, no file bytes,
no credentials.

### 4. Meanwhile, the agent has already emailed you

This is the part that changes how the product feels. **You do not have to come back to
the website to use it.** The welcome email introduces your agent and explains that you
give it work by replying to that address — and that you never need to sign in to do so.

It tells you what is happening right now (it is reading your Drive and Gmail, and will
write again the moment the graph is ready), how it scopes work, the two kinds of job it
does, and how to phrase a request.

Then it emails you again when ingestion finishes — and it distinguishes the endings
rather than flattening them:

> *"Good news. Your files are ingested and your knowledge graph is built and ready to
> use. Drive and Gmail both finished, covering 412 items that were ingested or already
> up to date."*

versus *"the scans finished but found no supported files"*, versus *"I ingested 412
items, and some files or a connector did not complete, so your graph is not fully ready
— reply 'retry ingestion' and I will pick up where I left off."* It never tells you the
graph is ready when it isn't.

### 5. You work by replying in plain English

You scope work to a project — one fund, one quarter, one job — and usually the fund name
and quarter are all it needs to find the right one.

**The two kinds of work it does:**

**"Do a first run-through for Fund A, Q2 2026."** — for a deliverable that doesn't exist
yet. A production agent drafts it as a workbook and writes down the delivery rules it
inferred, quoting the source text behind each one. A second, independent agent reviews
that draft against the same evidence and lists what remains unresolved. You get the
workbook, the rules, and a straight account of what is missing. Where it cannot find a
number it says so rather than filling the gap with a plausible one.

**"Run QC for Fund A, Q2 2026."** — for a deliverable that already exists. A fixed,
version-pinned checker runs your loader file or terms schedule against the source data.
The agents around it choose the inputs and explain the outcome; **they have no power to
overrule a finding.** Terms checks additionally refuse to run until a named person has
ratified the terms snapshot. Afterwards you get a dashboard link.

**Everything else you can ask:**

| You write | It does |
|---|---|
| *"What did the QC gate find?"* / *"Why was that blocked?"* | Reads back what is on the record. Runs nothing again, materialises nothing |
| *"What is the management fee basis for Fund A?"* | Answers from that project's own documents and quotes the source. If they don't answer it, it says so |
| *"How is that task going?"* — add *"with logs"* | Live status and current phase while something runs; with logs, the phase-by-phase event stream |
| *"How is my ingestion going?"* / *"Retry ingestion"* | Progress, or a restart that reuses everything already done |
| *"Show me the graph for Fund A"* / *"show me my knowledge graph"* | A link — you'll need to be signed in to open it |

**What it sends you:** a start email before it dispatches work, a completion email after
— including when the result is *blocked* or *failed*, never only on success — the draft
workbook itself, a link to the QC dashboard, a link to a graph, or a cited answer to a
question. First-run workbooks are also dropped into a **`Private markets drafts`** folder
in your own Google Drive, using a scope that only ever grants access to files this
application itself created; it can never read anything already in your account.

**How it works underneath:** your reply reaches an AgentMail inbox. A coordinator model
turns it into a real function call — `trigger_qc_gate`, `trigger_first_run`,
`explain_run`, `answer_project_question`, `check_workflow_status`, `check_ingestion_status`,
`retry_ingestion`, or one of the link tools — and dispatches a separate job through Cloud
Tasks. Each running job keeps a durable trace in its own project database: timestamped
phase transitions, evidence and artifact counts, checker state, delivery state. That
trace is what a later *"status?"* reads, which is why asking for status never starts a
second run.

Four things help it help you: name the fund and the quarter; send one request per email
(ask for two workflows at once and it will ask you to pick); if it names a missing input,
send it and ask again; and if your intent is ambiguous it asks rather than guesses.

One more: if someone it already recognises from your documents emails it directly, it
files their message and attachments into your graph and refreshes whichever project it
relates to. You don't forward things twice.

### 6. You read the result on the QC dashboard

`/dashboard` is the main product surface. Pick a project and you get one run:

- **Header** — the draft's filename and hash, the entity, the as-of date, which terms
  snapshot was used and how many facts were in force, the run ID, and which **turn** this
  is for this draft.
- **Scoreboard** — counts by tier, passes out of checks run, and the total amount at
  stake. Where both runs exist it shows the pair side by side: **"No brain"** (arithmetic
  only, no register) against **"Brain on"** (checked against the terms in force). The gap
  between those two columns *is* the value the register adds — demonstrated, not asserted.
- **Findings**, grouped and ordered by tier then by amount at stake:
  - **Tier a** — changes a balance, an allocation, or the scope
  - **Tier b** — changes a reported line, or must be resolved before release
  - **Tier c** — hygiene
  - **Decisions owed** — *not errors*: a blank the administrator must fill
  - **Passes** — shown, not hidden
  - **Not run in this mode** — skipped, never silently counted as passed
- **Each non-pass expands** to the evidence — the values compared — with a decision
  control: fix the draft, accept with a written reason, or escalate.
- **History strip** — `turn 1: 43 findings → turn 2: 11 → turn 3: 2`. Reducing that curve
  is the entire point of the product, so it is on the page.
- **Artifacts** — download the exact inputs, the checker's own code and output, the
  findings JSON, and a standalone Markdown report that reads correctly outside the system
  that produced it and can be attached to an email.

A run that produced no checks never renders a scoreboard reading "0 errors caught" — it
gets the header and the reason it stopped.

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
