# Design note: the QC gate and the knowledge graph

5 September 2026. For KG and partner. Companion to `DEMO_SPEC.md` and `DEMO_RUNBOOK.md`. Roles: partner owns product concept, architecture and infrastructure; KG owns eval and test sets, the gate, and the fixtures the contract test uses.

## Verdict

Buildable in hackathon time and demonstrable, on one condition: the graph's only job on the critical path is to answer one question, "what are investor X's terms in entity Y on date D, and where did each term come from". The QC gate stays a deterministic function of two inputs, a schedule and a terms snapshot. Everything else the graph might do (ingest emails, hold documents, draw pictures) sits behind that one question and can fail without breaking the demo.

## 1. The seam: one contract between the two builds

```
terms_as_of(entity_id, as_of_date) -> rows with exactly the columns of stage0_baseline/terms_table_v1.csv
                                     + recorded_at, fact_id (provenance handles)
```

- The gate calls this (or reads a CSV the graph exports in this shape). It never queries the graph directly.
- The partner's graph is "done" for the demo when its export for `as_of = 2026-06-30` equals `terms_table_v1.csv` and for `as_of = 2026-09-30` equals `terms_table_v2.csv`, row for row. That equality check is the contract test. Both sides build against the fixtures in parallel and meet at the test.
- The gate already has the seam: `eval/terms_checks.py --terms <csv> --as-of <date>`.

## 2. What a fact looks like (the audit chain lives here)

Every term in the graph is a fact with two clocks and a source:

| field | meaning | example (Trentcombe fee rate after the email) |
|---|---|---|
| subject, predicate, object | the term | investor:7335_02891, mgmt_fee_rate_pa, 0.0075 |
| valid_from, valid_to | when the term is in force in the real world | 2026-07-01, open |
| recorded_at | when the graph learned it | 2026-07-06 09:14 |
| source_document, source_clause | the paper it rests on | Side letter SL-TRENTCOMBE-2026-01, 2(a) |
| received_via | how the paper arrived | email:20260706091400.trentcombe.lammwick |
| supersedes | the fact it closed | the 0.0085 fact, valid_to set to 2026-06-30 |

Two clocks is the whole audit story. "Received 6 July, effective 1 July" answers both questions the fund manager asks: was the Q2 fee run on 30 June wrong (no, the term was not yet known or in force) and must the Q3 run change (yes, and here is the clause). The name for this is bitemporal data. Any store can hold it; it is two date columns and a discipline of never deleting, only closing.

Every check run is also recorded, so the chain runs end to end:

```
email -> document -> facts (closed/added) -> terms snapshot (as_of, hash) -> check run (schedule hash, results) -> finding
```

`run_id, run_at, entity_id, as_of, terms_snapshot_hash, schedule_file_hash, results_json`. With that row, "which fee runs used Trentcombe's old offset" is one query, and a rerun reproduces exactly.

## 3. The demo as three runs of the same gate

| Run | Terms input | What the gate says | What the audience learns |
|---|---|---|---|
| A. No brain | none (arithmetic checks only: shares, footing, gross = basis × rate / 4, totals) | Q2 admin draft: all PASS | Footing cannot see a side letter. This is the administrator's reality today |
| B. Brain on | `terms_as_of(2254, 2026-06-30)` from the graph | Q2 admin draft: TC03 FAIL, Trentcombe, 22,149.55 | The same schedule, now checked against what was agreed |
| C. Brain learns | email arrives, delta applied, `terms_as_of(2254, 2026-09-30)` | Q3 admin draft: TC01, TC02, TC05, TC09 FAIL, Trentcombe, 9,296.43 overcharged | Small change, captured once, applied to every future calculation. Click the finding, walk back to the email |

Scoreboard to put on screen: errors caught A 0, B 1, C 4. Say "accuracy" as "checked against the terms in force on the date of the calculation", not as a percentage.

## 4. Build split

**Exists (done, in this folder):** terms tables v1 and v2, entity terms, triples v1 full and v2 delta, LPA extract, side letters v1 and v2, the `.eml`, the Q2 and Q3 schedules (admin drafts and answer keys), `terms_checks.py` (nine checks), `eval_loader.py` (51 checks on the loader), spec and runbook.

**Partner (graph), in order of value:**
1. A store of the partner's choosing that holds the fact shape above (two dates per fact) and loads `kg_ingest/triples_v1_full.csv`. Infrastructure and architecture are the partner's call; the gate only depends on the fact shape and the export.
2. `apply_delta(triples_v2_delta.csv)`: close rows where `operation = close`, insert rows where `operation = add`, stamp `recorded_at`.
3. `terms_as_of(entity_id, date)` exporting the contract shape. Pass the contract test against v1 and v2.
4. The picture: before and after, the six changed facts highlighted, the path email to side letter to investor to term visible. This is the "wow"; budget real time for it.
5. Stretch only: extract the delta from the email and PDF with a model, show it to a human for confirmation, then apply. Keep the pre-built delta as the fallback and rehearse with it.

**KG (gate), small:**
1. `--arithmetic-only` mode in `terms_checks.py` for Run A (about 20 minutes).
2. A `run_gate.py` wrapper that writes the run record (hashes, as-of, results JSON) to a `runs/` folder in the triple shape so the graph can ingest its own audit trail.
3. The contract test: a script that diffs the graph export against the fixture and prints equal or the differing cells.

**Joint:** runbook v2 with runs A, B, C; one rehearsal with every fallback exercised.

## 5. Where it fails, and what to do

| Risk | Likelihood | Fallback |
|---|---|---|
| Live model extraction gets a clause wrong on stage | high | Pre-built delta; extraction is a stretch slide |
| Graph store will not start or the query is slow | medium | `terms_as_of` also reads the fixture CSVs; the gate never notices |
| Terms export drifts from the contract (a renamed column) | medium | Contract test run before the demo, not during |
| Gate runs without terms and silently passes | low but fatal | Gate refuses to run in terms mode without an as-of snapshot; arithmetic-only is an explicit flag |
| Picture is not ready | medium | Show the delta CSV with the six rows highlighted; it is the same content |

## 6. What changes when this becomes a product, not a demo

- The graph becomes the system of record for confidential per-investor terms. That means access control per investor, backup, and a maker-checker step: an administrator confirms an extracted change before it becomes a fact, and the confirmation is itself recorded.
- Never delete a fact. Corrections close the wrong fact and add the right one, with a reason.
- Every calculation the administrator produces carries the terms snapshot hash it was computed against, so a statement can always be re-derived.
- Whatever the store, keep the two-date, never-delete discipline on facts; that is what makes every past statement re-derivable.

## 7. Open decisions

1. Whether the partner's store holds two dates per fact (valid and recorded); if only one, agree which and record the other in the document node.
2. Whether the email is opened on stage by a person (recommended) or watched by a process.
3. Whether the run records go into the graph for the demo (nice) or stay as files (fine).
