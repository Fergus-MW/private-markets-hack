# KG handover: entity set for the QC knowledge graph

For Fergus. This folder settles the object classes and edges for the graph half of the build, grounded in the two datasets we actually have: one quarter of investor-level ledger (Q2 2026, 79 co-invest entities, 33,902 rows) with the administrator's finished loader, and the terms and side-letter demo (Kestrel Lammwick, 18 investors, stages 0 to 2).

Open `fund-admin-graph-ontology.html` in a browser for the full page with the two diagrams. This README is the same content in a form GitHub renders.

## What is in the folder

| File | What it is |
|---|---|
| `fund-admin-graph-ontology.html` | The ontology page: entity map, demo trace, class inventory, modelling rules, SurrealDB sketch, the three test queries, open decisions |
| `KG_INTEGRATION_DESIGN.md` | The design note agreed on 5 Sep 2026: the seam between gate and graph, the fact shape, the three demo runs, the build split, fallbacks |
| `kg_ingest/SCHEMA.md` | Shape of the staging triples and the temporal and provenance rules |
| `kg_ingest/triples_v1_full.csv` | Stage 0: every fact known at 30 June 2026 (337 rows) |
| `kg_ingest/triples_v2_delta.csv` | Stage 2: what the 6 July email changes (17 rows: 7 close, 10 add) |
| `fixtures/terms_table_v1.csv` | What `terms_as_of(2254, 2026-06-30)` must export, row for row |
| `fixtures/terms_table_v2.csv` | What `terms_as_of(2254, 2026-09-30)` must export, row for row |
| `fixtures/entity_terms_v1.csv` | Entity-level default terms for Kestrel Lammwick (LPA economics); investor facts override these |

Everything in the demo files sits on real anonymised investors and real allocation shares; commitments, side letters, people, email addresses and the GP name are synthetic (`.example` domains).

## The one thing on the critical path

```
terms_as_of(entity_id, as_of_date) -> rows with exactly the columns of fixtures/terms_table_v1.csv
                                     + recorded_at, fact_id
```

The gate never queries the graph directly. It reads this export. The graph is done for the demo when the export for 30 June 2026 equals `terms_table_v1.csv` and for 30 September equals `terms_table_v2.csv`. In the ontology that export is one query over term facts filtered by validity, pivoted from long to wide, then hashed.

## Classes, by layer

**Structure.** Fund Family · Legal Entity · Vehicle · Investor Account · Organisation (typed roles: investor, portfolio company, supplier, bank, administrator, auditor) · Person · Deal · Position · Bank Account.

Three splits that matter. "Fund" is three things: the family, the legal entity (the accounting unit) and the investor account (one investor's stake in one entity, where terms, fees and statements live). "Company" is one Organisation class with roles, not four classes. People are absent from the ledger and present in the process and the correspondence.

**Accounting reference.** System · Identifier (system-scoped, never a bare number) · GL Account · Trans Type · Batch Type (with priority) · Currency · Period · **Crosswalk** (one source account + trans type mapped to a target account, trans types, default batch type, with a status: exact, fallback, gap, approved). The crosswalk is where most of the migration's judgement sits: 105 rows, 2 open gaps, 389 rows mapped on trans type alone.

**Rules and term facts.** **Term Fact** (subject, predicate, value, valid_from, valid_to, recorded_at, version; never deleted, only closed) · Rule (batch-type priority override, mandatory supplier, zero-row exclusion) · Allocation Rule · Terms Snapshot (in-force facts for one entity on one date, pivoted and hashed; what the gate consumes).

**Ledger events.** Batch → Journal Entry → Transaction Line → Investor Allocation. Event kinds (call, distribution, valuation, cashbook, partner transfer) are Batch Type values, not classes.

**Process, reports and evidence.** Project · Report / Deliverable (content-hashed) · Report Line · Check (definition, with tier) · Gate Run (one execution: as_of, entity, schedule hash, snapshot hash, mode) · Check Result (status, tier, amount) · Finding · Decision · Document (existing table; kind covers side letter, LPA, email, statement) · Chunk (the stable citation unit the ingestion service already emits).

## Seven modelling decisions

1. **Two clocks, never delete.** Every term fact, rule and crosswalk row carries in-force dates and a known-since timestamp. Changes close the old fact and add a new one. "Received 6 July, effective 1 July" answers both of the fund manager's questions.
2. **Project is an instance, not a schema.** Its contents are shared classes tagged with the project, so cross-period questions stay answerable.
3. **The crosswalk is a node.** A lookup table hides the judgement; a node lets a finding point at one row.
4. **Everything actionable cites a chunk.** Term facts, findings, decisions and rules point at chunks using the ingestion service's stable IDs. That edge is the provenance panel.
5. **The gate reads a snapshot, not the graph.** Every gate run records the snapshot hash and the report hash it used.
6. **Lines stay in tables.** Transaction lines and allocations are plain records; batches, aggregates, runs and findings are graph nodes.
7. **Identifiers are system-scoped.** Objects relate to identifier records that carry the system and the value.

## The demo, traced through the graph

Stage 2, right to left from the finding: the gate run names the snapshot it used and the schedule it checked; the snapshot is the in-force facts for the entity on the as-of date; each fact names the clause it rests on; the clause sits in the letter that superseded the old one; the letter arrived attached to an email from a named person.

| Demo moment | Path through the ontology |
|---|---|
| Load v1 | Investor Account *in* Legal Entity; Term Facts on both, each citing an LPA or side-letter clause |
| Fee rate on 15 May 2026 → 0.0085 | Term Fact with valid_from ≤ date ≤ valid_to, *cites* Chunk *of* Document |
| Export terms_as_of(2254, 30 Jun 2026) | Terms Snapshot from in-force facts for every account in the entity |
| The email | Close 7 facts at 30 June, add 10 from 1 July, stamp recorded_at; Document v2 *supersedes* v1, *received via* the Email |
| Fee rate on 15 Aug 2026 → 0.0075 | Same query, different date; walk cites → Document → received via → Email |
| Run C on the Q3 draft | Gate Run *verifies* Report and *raises* Findings; TC09 overcharge 9,296.43 plus TC01, TC02, TC05 |
| Stretch: model extracts the delta | Proposed facts with no valid_from until a Decision approves them; the Decision cites the chunk and names the approver |

### How the triples map

| In the triples | In the ontology |
|---|---|
| `investor:<RFX ID>` | Investor Account, identified by an Identifier with system `rfx` |
| `invests_in entity:2254` | Investor Account *in* Legal Entity; 2254 is an Identifier with system `corvus` |
| `target_account_id` | A second Identifier on the same account, system `corvus`; equals the loader's Investor Account ID |
| `mgmt_fee_rate_pa`, `fee_basis`, `fee_inside_commitment`, `fee_offset_pct`, `fee_exempt`, `mfn`, `cas_deadline_days` | Term Facts on the Investor Account; entity defaults (`*_default`) are Term Facts on the Legal Entity |
| `commitment`, `commitment_share`, `called_31mar2026`, `unfunded_31mar2026` | Term Facts, the date in the predicate becoming valid_from |
| `notices_contact`, `notices_email` | Term Facts whose object is a Person with a notices role |
| `governed_by document:<ref>` | Investor Account *governed by* Document, dated |
| `rdf:type SideLetter`, `supersedes`, `received_via email:<id>` | Document kind; Document *supersedes* Document; Document *received via* a Document of kind email |
| `valid_from`, `valid_to`, `operation close/add`, `version` | Fact validity, the close and add operations, version label; `recorded_at` stamped on load |
| `source_document`, `source_clause` | Term Fact *cites* Chunk, falling back to the Document |
| `run:<id> GateRun`, `as_of`, `schedule_sha256`, `terms_snapshot_sha256`, `result:TCxx` | Gate Run fields; each result a Check Result; a FAIL becomes a Finding |

## SurrealDB sketch

Syntax follows SurrealDB 2.x; check keywords against the version on the VM.

```sql
-- Nodes
DEFINE TABLE legal_entity SCHEMAFULL;
DEFINE FIELD name     ON legal_entity TYPE string;
DEFINE FIELD currency ON legal_entity TYPE string;

DEFINE TABLE investor_account SCHEMAFULL;
DEFINE FIELD name ON investor_account TYPE string;

-- Identifiers: system-scoped, unique per system
DEFINE TABLE identifier SCHEMAFULL;
DEFINE FIELD system ON identifier TYPE string;   -- corvus | helio | rfx
DEFINE FIELD value  ON identifier TYPE string;
DEFINE INDEX uniq_identifier ON identifier FIELDS system, value UNIQUE;

-- Term facts: two clocks, never deleted
DEFINE TABLE term_fact SCHEMAFULL;
DEFINE FIELD subject     ON term_fact TYPE record<investor_account|legal_entity>;
DEFINE FIELD predicate   ON term_fact TYPE string;
DEFINE FIELD object      ON term_fact TYPE any;
DEFINE FIELD valid_from  ON term_fact TYPE datetime;          -- in force from
DEFINE FIELD valid_to    ON term_fact TYPE option<datetime>;  -- closed, not deleted
DEFINE FIELD recorded_at ON term_fact TYPE datetime;          -- known since
DEFINE FIELD version     ON term_fact TYPE string;
DEFINE INDEX fact_lookup ON term_fact FIELDS subject, predicate, valid_from;

-- Documents: the existing table, plus kind and email fields
DEFINE FIELD kind        ON document TYPE option<string>;  -- side_letter | lpa | email | statement
DEFINE FIELD received_at ON document TYPE option<datetime>;
DEFINE FIELD sender      ON document TYPE option<record<person>>;

-- The gate's audit trail
DEFINE TABLE gate_run SCHEMAFULL;
DEFINE FIELD as_of                ON gate_run TYPE datetime;
DEFINE FIELD entity               ON gate_run TYPE record<legal_entity>;
DEFINE FIELD schedule_sha256       ON gate_run TYPE string;
DEFINE FIELD terms_snapshot_sha256 ON gate_run TYPE option<string>;
DEFINE FIELD mode                 ON gate_run TYPE string;   -- terms | arithmetic-only
DEFINE TABLE check_result SCHEMAFULL;
DEFINE FIELD run    ON check_result TYPE record<gate_run>;
DEFINE FIELD check  ON check_result TYPE string;
DEFINE FIELD status ON check_result TYPE string ASSERT $value IN ['PASS','FAIL','SKIPPED'];
DEFINE FIELD tier   ON check_result TYPE string;
DEFINE FIELD amount ON check_result TYPE option<decimal>;

-- Edges
DEFINE TABLE in_entity     TYPE RELATION IN investor_account OUT legal_entity;
DEFINE TABLE identified_by TYPE RELATION IN legal_entity|investor_account|deal|position|gl_account OUT identifier;
DEFINE TABLE cites         TYPE RELATION IN term_fact|finding|decision|rule OUT chunk|document;
DEFINE TABLE closes        TYPE RELATION IN term_fact OUT term_fact;
DEFINE TABLE supersedes    TYPE RELATION IN document OUT document;
DEFINE TABLE received_via  TYPE RELATION IN document OUT document;   -- letter -> email
DEFINE TABLE governed_by   TYPE RELATION IN investor_account OUT document;
DEFINE TABLE maps_from     TYPE RELATION IN crosswalk OUT trans_type;
DEFINE TABLE maps_to       TYPE RELATION IN crosswalk OUT trans_type;
DEFINE TABLE allocated_for TYPE RELATION IN investor_allocation OUT investor_account;
DEFINE TABLE verifies      TYPE RELATION IN gate_run OUT report;
DEFINE TABLE raises        TYPE RELATION IN gate_run OUT finding;
DEFINE TABLE about         TYPE RELATION IN finding OUT investor_account|crosswalk|batch|report_line|legal_entity;

-- Stage 2: apply the delta. Close, then add, then link.
UPDATE term_fact SET valid_to = d'2026-06-30'
  WHERE subject = investor_account:⟨7335_02891⟩ AND predicate = 'mgmt_fee_rate_pa' AND valid_to IS NONE;
LET $new = CREATE ONLY term_fact SET
  subject = investor_account:⟨7335_02891⟩, predicate = 'mgmt_fee_rate_pa', object = 0.0075,
  valid_from = d'2026-07-01', recorded_at = d'2026-07-06T09:14:00+01:00', version = 'v2';
RELATE $new->cites->chunk:⟨SL-TRENTCOMBE-2026-01·2a⟩;
RELATE document:⟨SL-TRENTCOMBE-2026-01⟩->supersedes->document:⟨SL-TRENTCOMBE-2024-01⟩;
RELATE document:⟨SL-TRENTCOMBE-2026-01⟩->received_via->document:⟨email-20260706091400⟩;
```

## Three queries the gate needs, and the first two are asked on stage

```sql
-- 1. Trentcombe's fee rate on a date; run with 15 May and 15 Aug 2026, expect 0.0085 then 0.0075
SELECT object, valid_from, recorded_at, ->cites->chunk AS clause
FROM term_fact
WHERE subject = investor_account:⟨7335_02891⟩ AND predicate = 'mgmt_fee_rate_pa'
  AND valid_from <= $on AND (valid_to IS NONE OR valid_to >= $on);

-- 2. The terms snapshot: long form, pivot to the fixture columns on export, hash the result
SELECT subject, predicate, object, id AS fact_id, recorded_at
FROM term_fact
WHERE subject->in_entity->legal_entity CONTAINS legal_entity:⟨2254⟩
  AND valid_from <= $as_of AND (valid_to IS NONE OR valid_to >= $as_of);

-- 3. Which fee runs used the old terms
SELECT id, as_of, terms_snapshot_sha256, ->verifies->report AS report
FROM gate_run
WHERE entity = legal_entity:⟨2254⟩ AND as_of >= d'2026-07-01'
  AND terms_snapshot_sha256 != $v2_snapshot_hash;
```

Query shapes are illustrative and have not been run against a live instance.

## Open decisions

1. **Both clocks or one.** If the store can hold only one date pair, keep valid dates on the fact and put recorded_at on the document node. Agree before loading the fixture.
2. **The maker-checker step.** Extracted facts sit without a valid_from until a Decision approves them; the approval is itself a fact with a person on it. The pre-built delta is the fallback for the demo.
3. **Run records in the graph or as files.** The gate writes runs in triple shape already.
4. **Where the ledger lines live.** Records alongside the graph, or aggregates in the graph pointing back at the loader file.
5. **Investor identity.** Organisation carries the Corvus common ID, Investor Account the specific ID and the registrar ref. Agree the merge key when names differ.

## Provenance

Built 5 September 2026 from the Ylookup hackathon datasets (anonymised) and the Claude builds in KG's `Input/Claude output/` folder: the Tranche 1 loader rebuild (reconciles to the administrator's v4c on every column except batch numbering) and the terms and side-letter demo. The `document` table and stable chunk IDs are what the ingestion service in this repo already writes; nothing else described here exists in the database yet.
