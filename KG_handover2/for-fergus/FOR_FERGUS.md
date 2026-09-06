# For Fergus: five minutes, three decisions, two build items

Written overnight by KG's agent so the morning is integration and rehearsal, not building. Everything referenced is in this pull request.

## What the transcripts and the research say, in one paragraph

The fund manager in call 1 does not read what arrives; he puts it through a checking tool first. The preparer at the administrator is the harder user and dismisses a tool after two false positives. What earns both of them is a checklist they can see, with the passes shown as passes (the green ticks), and the evidence in the format they already work in, which is Excel with cell comments. Your PRD already says this: F5 defines the findings page, F5.7 orders by tier then amount, F4.7 shows passes. The industry wants a GUI checklist as the visible form of the gate, and Excel as the working copy.

## One record, three surfaces

The gate writes one run record per draft per run (`run.json`: fingerprints of draft and terms snapshot, every check with tier, status, evidence, amount). Three renderings of that record, all built tonight, none calling a model:

1. **The page** (`/gate/…html`): scoreboard, findings by tier, passes, decision controls. The only place a decision is recorded. This is PRD F5.1 to F5.8.
2. **The workbook** (`…_QC.xlsx`): the administrator's own draft, copied, with a green QC sheet in front and a comment on every offending cell. This is what the preparer opens.
3. **The notification** (`notification_….txt`): six lines for chat or email with a link to the page. Never a decision surface.

Your pipeline stays fully agentic on ingestion, extraction, proposal and running the gate. A person ratifies a term (F3.2), accepts a finding (F5.6), and approves a decision request (F6.4). The line for the stage: **the agent runs the gate, a person runs the fund.**

## Three decisions (yes or no is enough)

| # | Decision | Default if you say nothing |
|---|---|---|
| D1 | Merge this pull request. It adds files only: `KG_handover2/` and `frontend/public/gate/`. It touches none of your files | Merge |
| D2 | Adopt "one record, three surfaces" as the front-end pattern, replacing the first paragraph of build plan section 4b (text below) | Adopt |
| D3 | Add a link to `/gate/` on the connect screen (three lines, snippet below). Purely cosmetic for the demo flow | Skip if you would rather not touch the screen |

**Proposed 4b paragraph.** "The gate's human surface is a checklist with evidence, one page per draft per run, generated from the run record as a static file; the same record also renders as a QC sheet with cell comments inside the administrator's own workbook, and as a plain-text notification for chat or email. Decisions are recorded only on the page. The graph's picture and provenance panel are the partner's UI; the page links to them by run id and fact id."

**Connect-screen snippet (D3).** In `frontend/index.html`, after the `auth-status` paragraph: `<a class="gate-link" href="/gate/">Open the QC gate</a>`. In `frontend/src/style.css`: `.gate-link { display: inline-block; margin-top: 22px; font-size: 13px; font-weight: 550; color: #f5f1ec; opacity: .72; text-decoration: none; } .gate-link:hover { opacity: 1; }`.

## Two build items for today, with acceptance

| # | Item | Acceptance | Notes |
|---|---|---|---|
| B1 | The four fixture CSVs in your **live** graph, via the Drive connector: `entity_aliases.csv` and `entity_terms_v1.csv` with no extras; `terms_table_v1.csv` with `fund_id` and `snapshot_as_of` `2026-06-30`; `terms_table_v2.csv` with the same `fund_id` and `snapshot_as_of` `2026-07-01` | `GET /graph/funds/<key>/terms?as_of=2026-06-30` and `…=2026-09-30` saved to JSON, and `KG_handover2/built-tonight/eval/contract_test.py --export <that json> --fixture <fixture csv>` prints **equal** twice | KG shares the Drive folder in the morning. There is no CSV upload endpoint, so this is the only live path. In-process it already passes (`graph_export/`), so the risk is only the sync |
| B2 | Nothing else. Merge D1; if D3, add the snippet | `npm run dev` in `frontend`, open `/gate/`, click through | |

Optional, only if B1 is done inside the first hour: a provenance endpoint for one finding (`GET /graph/provenance?fact_id=…` or by source id) returning the chain email, document, facts closed and added, snapshot. The Q3 page's "open in graph" buttons currently point at `http://127.0.0.1:18080/graph/sources`, so any URL shape you return can be wired in by changing one `--graph-url` argument.

## What not to build today

- A provenance panel UI. The Q3 page renders one from the delta triples (rate, basis, offset with old values and clauses; in force from, known since, via which email).
- Chat delivery, the ratification queue UI, the workbook writer. Surfaces 2 and 3 exist as scripts; delivery channels are a sentence on stage ("the channel is swappable").
- Anything that changes the checker's behaviour at run time. F7.1.

## The seam, unchanged

`terms_as_of(entity_id, as_of_date)` returns rows with exactly the columns of `KG_handover/fixtures/terms_table_v1.csv`. Your endpoint `GET /graph/funds/{fund_id}/terms?as_of=` already returns `{fund_id, as_of, rows, provenance}` in that shape, and the checker now consumes it directly (`--terms-url`). The contract test is the definition of integrated, and it passed tonight against your code.

Agent-ready prompts for each item are in `AGENT_PROMPTS.md`.
