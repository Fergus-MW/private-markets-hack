# Knowledge-graph staging files

Two CSVs, one shape, loadable by any graph or table store.

`triples_v1_full.csv` (stage 0): every fact known at 30 June 2026. Columns: `subject`, `predicate`, `object`, `valid_from`, `valid_to` (blank = open), `source_document`, `source_clause`, `version`.

`triples_v2_delta.csv` (stage 2): what the 6 July 2026 email changes. Same columns plus `operation`: `close` sets `valid_to` on an existing fact, `add` inserts a new fact valid from 1 July 2026. Applying the delta to v1 gives the state of the world after the email. Rows: 17.

Subjects: `investor:<RFX ID>` (the source system's investor-in-vehicle ID, which also joins to the loader via `target_account_id`), `entity:2254` (the target system's legal entity ID), `document:<side letter ref>`.

Predicates on investors: rdf:type, name, invests_in, target_account_id, commitment, commitment_share, called_31mar2026, unfunded_31mar2026, mgmt_fee_rate_pa, fee_basis, fee_inside_commitment, fee_offset_pct, fee_exempt, mfn, cas_deadline_days, notices_contact, notices_email, governed_by. Predicates on the entity: the rows of `stage0_baseline/entity_terms_v1.csv`.

Temporal rule: a question about terms on date D reads the facts with `valid_from <= D` and (`valid_to` blank or `valid_to >= D`). Ask the graph "Trentcombe management fee rate on 15 May 2026" and "on 15 August 2026": the answers must differ (0.0085 then 0.0075).

Provenance rule: every fact carries the document and clause it came from. The email is a document node that the v2 side letter was `received_via`.
