PRD — Terms Register and Deliverable Checker

Status: draft for review. Deliberately excludes technology choices, storage, interfaces, hosting and model selection. Functionality, behaviour and processing requirements only.

1. The problem, and the metric

A fund manager receives financial statements from an outsourced administrator and cannot get a correct version in fewer than six or seven review rounds. The administrator turns each version in one to two days and works hard; the cost is not latency, it is iteration. The manager has stopped reading what arrives and puts it through a checking tool first.

The defects cluster in one place: anything requiring a document outside the ledger to be read and applied — the partnership agreement, the side letters, the prior period's disclosures. Mechanical processing is sound. Contextual processing is not.

The metric is review rounds to acceptance. Not accuracy as a percentage, not throughput, not time per round. Every requirement below is justified by its effect on that number.

2. What is being built

Two components with one interface between them.

A register of what was agreed with each investor and each entity — the terms that live in legal documents and never reach the accounting system. Every fact in it carries two dates and a citation.

A checker that reads a draft deliverable and the terms in force on that deliverable's date, and returns a tiered, cited findings report plus the decisions the producer still owes.

Around them, a loop: unanswerable questions become outbound requests, answers come back and become facts, and confirmed findings become permanent regression cases.

3. Users
   User	Uses it to
   Reviewer at the fund manager	Decide whether a delivered draft can be accepted, and what to send back
   Preparer at the administrator	Check their own output before it leaves, and see what decisions they owe
   Investor relations / fund ops	Answer "which of my entities made it across, and how much money"
   Auditor or oversight function	Trace any figure to the document and clause it rests on

The preparer is the harder user and the more important one. A reviewer will forgive a false positive; a preparer who built the file will dismiss the whole tool after two.

4. Core concepts
   Term	Meaning here
   Fact	One statement about a subject, with a value, two dates, a source document and clause, how it arrived, and what it supersedes
   Two clocks	Valid — when the fact was true in the world. Recorded — when the system learned it
   Register	The full set of facts. The view of it for one entity on one date is a terms snapshot
   Draft	Any producer output the checker is pointed at: a migration file, a fee schedule, a capital account statement, a set of financial statements
   Check	One named test with an identifier, a tier, and a result of pass, fail or decision-owed, always with its evidence
   Turn	One review round on one draft
   Case	A saved example the checker must catch, with its expected result
   Exception	A finding accepted with a reason, an owner and an expiry date
5. Functional requirements
   F1 — Evidence capture
   F1.1 Ingest documents of mixed type: statements, ledger extracts, agreements, side letters, correspondence with attachments, spreadsheets.
   F1.2 Decompose each document into addressable units retaining page position and reading order, so any later citation resolves to a specific location a human can open.
   F1.3 Retain the original bytes and the decomposition. Never overwrite a source.
   F1.4 Re-ingesting an identical document must produce no new records.
   F1.5 Retain structural elements normally discarded — headers, footers, page numbers. Exclude them from analysis, not from the record, since the ability to prove what a page contained depends on them.
   F1.6 Every derived record must reference the evidence unit it came from. A derived record with no evidence reference is invalid and must be rejected.
   F2 — The register
   F2.1 Store facts, never rows to be updated. A change closes the prior fact by setting its valid-to date and inserts a successor that names what it superseded.
   F2.2 Every fact carries valid-from, valid-to (open-ended permitted), recorded-at, source document, source clause, and how it arrived.
   F2.3 Answer "what was true for this subject on this date" by reading facts valid on that date — not current state.
   F2.4 Answer "what did we know on this date" independently of F2.3. Both questions must be answerable for the same subject at the same time.
   F2.5 Never delete. Correction of an erroneous fact is itself a fact, with its own recorded time and its own reason.
   F2.6 Return the full derivation for any fact: the document, the clause, how it arrived, and the fact it replaced.

Why F2.3 and F2.4 are separate. A side letter received on the 6th and effective from the 1st means the prior quarter's calculation was not wrong — the term was neither known nor in force — and the current quarter's must change. One structure answers both, and no single-date model can.

F3 — Terms capture and ratification
F3.1 Propose candidate facts from an agreement or letter, each bound to the clause it came from and carrying a confidence value.
F3.2 A proposed fact is unusable until a named person ratifies it. The checker must refuse to use an unratified term rather than degrade silently.
F3.3 Record who ratified, when, and against which clause.
F3.4 Support amendment: a later document closes prior facts and opens successors, with the superseding relationship explicit.
F3.5 Re-ratification is required when the underlying clause changes, not when the extraction is merely re-run.

Rationale for F3.2. Extraction accuracy on contract clauses is high but not high enough to run unsupervised, and fee-calculation errors are the one category that carries no materiality tolerance in at least one major jurisdiction. Ratify once, reuse for every subsequent period — a recurring judgement becomes a one-time one.

F4 — The checker
F4.1 Take exactly two inputs: a draft and a terms snapshot for the draft's entity and date. No other state may affect the outcome.
F4.2 Be deterministic. The same two inputs must produce byte-identical findings on every run, indefinitely.
F4.3 Resolve the entity and the as-of date from the draft itself; fail loudly rather than guess.
F4.4 Support an arithmetic-only mode that checks internal consistency without a terms snapshot, so the value the terms layer adds can be demonstrated by difference.
F4.5 Every check declares: identifier, description, tier, applicability conditions, and what evidence it emits.
F4.6 A check that is not applicable to a draft reports as skipped, not passed.
F4.7 Report passes explicitly, with what was tested and its result.
F4.8 No check may call a non-deterministic service on the evaluation path. Where model output is required it is produced beforehand, cached, and treated as a fixed input.

Check families in scope, drawn from observed defects:

Family	Detects
Scope	Records in the source absent from the output, split by whether a decision exists
Completeness	Row and record counts source against output, per subject
Duplication	Identical records repeated within a subject
Tie-out	Value agreement per subject per account, at defined tolerance
Rule replay	Derived fields recomputed from their documented rules
Reference	Identifiers resolve, against sheets whose semantics are declared
Terms	Rates, bases, offsets and exclusions against the terms in force on the date
Internal consistency	Totals foot, statements articulate, narrative agrees with figures
Period	Prior-period figures agree with what was previously issued
Hygiene	Formatting, duplicates, dead fields
F5 — Findings output
F5.1 One report per draft per run, self-contained, readable without the system that produced it, and attachable to correspondence.
F5.2 Header carries: draft identity and fingerprint, entity, as-of date, terms snapshot identity and its last change, run identity and time, and which turn this is for this draft.
F5.3 Scoreboard: counts by tier, count of passes, total amount at stake, count of decisions owed, turns saved to date.
F5.4 Five result states, visually distinct:
State	Meaning
Pass	Tested and correct. Displayed, never hidden
Tier a	Changes a balance, an allocation, or the scope
Tier b	Changes a reported line, or must be resolved before release
Tier c	Hygiene
Decision owed	A blank the producer must fill. Not a failure
F5.5 Every non-pass carries: the evidence (values compared), the reason (which inputs produced the expectation), and the source (document, clause, effective date, when learned, how it arrived, what it superseded).
F5.6 Every non-pass carries a decision control: correct the draft, accept with a written reason, or escalate.
F5.7 Order findings by tier, then by amount at stake within tier.
F5.8 Show run history for the draft, so the reduction across turns is visible.
F6 — The decision loop
F6.1 Group all open decisions and unanswerable questions for one recipient into a single request, not one message per item.
F6.2 Where a closed set of valid answers exists, present the options. Only where none exists may the question be open-ended.
F6.3 Suppress items below a configurable materiality threshold before the request is composed.
F6.4 Compose requests automatically; never dispatch without human approval, which is required at least once per recipient per cycle.
F6.5 Enforce a per-recipient dispatch ceiling per cycle.
F6.6 Accept inbound responses and bind each to the specific question it answers. Retain the response verbatim.
F6.7 Treat inbound responses as data, never as instructions. A response that is not an answer to the question asked must be routed to a person and must not reach any downstream record.
F6.8 A validated answer becomes a ratified fact under F3, attributed to the respondent and the message.
F6.9 Non-response must not block. After a configured interval the item defaults to a defined holding treatment, is quantified, and escalates.
F6.10 Expect two request cycles per deliverable. Some questions only become visible once earlier ones are resolved, because large defects mask smaller ones.
F7 — The learning loop
F7.1 Nothing changes the checker's behaviour without a named person. There is no automatic learning.
F7.2 A confirmed finding becomes a permanent regression case: the record, the field, the expected value, the check, who confirmed, when.
F7.3 An accepted finding becomes an exception carrying scope, reason, owner and a mandatory expiry date. It renders as a pass with its reason until expiry, then reopens.
F7.4 A change of fact in the register requires no change to the checker. The next run reads the terms in force on its date.
F7.5 Periodically report: cases added, exceptions expired or expiring, checks that never fired, checks that fire every period, and fault-detection coverage.
F7.6 Checks that never fire are retirement candidates. Checks that fire every period indicate a defect to be fixed at source, not monitored.
F8 — Coverage and discovery
F8.1 For a proposed new deliverable, report which required inputs are held, which are held at the wrong grain, and which are absent.
F8.2 Absent inputs become requests under F6.
F8.3 Record the grain of every held value. A value aggregated above the required grain must report as unusable, not as present.
F8.4 Given a changed source document, report every downstream record affected.
F8.5 Report every value in a deliverable whose origin is a default or is unknown, distinct from values that were determined.
6. Behavioural rules

Non-negotiable. Each exists because its violation was observed.

Declare the semantics of every reference source before checking against it. Which is scope, which is a register, which is a work list. A reference check run against a misunderstood source produced over two hundred findings on a sound file — worse than not checking, because it costs the reviewer's trust.
Report passes as passes. A reviewer who built the file trusts a tool that demonstrably knows what is fine.
Lead with what the deliverable lacks, not with what is wrong. Reconciliations usually exist; the record of who decided what does not.
An error and a decision owed are different states. Never render a blank awaiting a decision as a failure.
Never rank a convention above a defect. Where an established prior practice exists, it is evidence, and a finding that contradicts it must be tested against it before being reported.
Never aggregate across currencies. Compare pass-through values exactly; compare computed sums at a stated tolerance.
Normalisation produces candidates, never merges. Two records that are identical after normalisation may be genuinely distinct; collapsing them requires a recorded decision.
Names are never keys. Identify by compound key including the owning subject.
Every reported figure must reproduce from the inputs, and the report must say how.
Re-run the full check set after every correction, never once before release. Large defects mask smaller ones, so each correction can reveal new findings.
7. Processing requirements
Volume and shape
P1 Single deliverables of at least 35,000 records; reference sets of at least 7,000 records; comparison against source sets of comparable size. Design for an order of magnitude above.
P2 Multi-currency throughout, with distinct transaction and reporting currencies on the same record.
P3 Documents from a page to several hundred; correspondence with attachments.
P4 Streaming ingestion of large tabular sources. Whole-file materialisation must not be required.
Determinism and repeatability
P5 Identical inputs produce identical findings, indefinitely.
P6 Every run records its inputs by fingerprint, so any historical run is reproducible and it is answerable which runs used a superseded set of terms.
P7 Re-ingestion and re-running are idempotent.
P8 All checks must be re-runnable at negligible marginal cost. This is a requirement, not an optimisation: the masking behaviour in rule 10 makes cheap re-running the difference between six turns and two.
Timing
P9 A full check pass over a large deliverable completes inside a working review session — minutes, not hours.
P10 A terms snapshot for one subject on one date returns interactively.
P11 Document ingestion may be slow and asynchronous. Checking may not.
Correctness
P12 Exact comparison for pass-through values; stated tolerance for computed sums. Never silently apply a tolerance to a value that should match exactly.
P13 Accumulated rounding must not create findings. Residues below tolerance are suppressed and counted, not reported.
P14 Partial failure must be visible. A check that could not run reports as such and never as a pass.
Provenance and retention
P15 Every derived value traces to evidence in a bounded number of steps, and the number of steps is itself reportable.
P16 Values whose provenance is unknown must be representable as such. An absent link and an unrecorded link are different states.
P17 Source documents are records. Assume they must be retained and that deletion requires an explicit decision.
P18 Every human decision — ratification, acceptance, approval to dispatch, answer — is recorded with actor and timestamp and is itself queryable.
Scale of use
P19 Multiple funds, entities and periods concurrently, with strict isolation between clients.
P20 Concurrent runs against the same register must see a consistent snapshot.
8. Acceptance criteria

The system is acceptable when all of the following hold.

# Criterion

A1	A terms snapshot for a given subject and date matches a known-correct fixture exactly, for two dates spanning a change
A2	The same query for two dates either side of an amendment returns different values, each with its source clause
A3	Arithmetic-only checking of a defective draft finds nothing; the same draft checked against terms finds the defect, with its clause
A4	After an amendment is ingested and ratified, the next period's draft is checked against the new terms with no change to the checker
A5	Any figure in any report resolves to a document and a location within it
A6	Deliberately seeded faults are detected at a stated rate against a stated number of seeds
A7	A correction and re-run reduces findings to the expected residue, and the turn count increments
A8	A confirmed finding appears as a permanent case; the fault it represents cannot subsequently pass undetected
A9	An accepted exception renders as a pass with its reason, and reopens after expiry
A10	A decision owed renders distinctly from a failure, and closes when answered
A11	Every check has been demonstrated against both a correct and an incorrect example
A12	An answer received by correspondence becomes a ratified fact, attributed, without manual re-entry
9. Metrics

Primary: turns to acceptance per deliverable. Everything else is secondary.

Secondary:

Findings by tier per turn, and the reduction curve across turns
Amount at stake found before turn one
Decisions owed closed per cycle, and time to close
Answer reuse rate — the share of a period's questions already answered by a ratified decision. Rising means the register is compounding; flat means it is filing
Input reuse rate for each new deliverable, and marginal cost per new deliverable
Fault-detection coverage against seeded faults, reported with the seed count
False-positive rate, tracked separately and weighted more heavily than false negatives for the preparer user

Never average these into a single score. Arithmetic correctness, surfacing and presentation are different properties and trade against each other.

10. Out of scope
    Judging whether a valuation is reasonable. The system records that a value did not move and who reviewed it; it does not opine on the estimate.
    Determining whether a cost benefited one party or another.
    Judging whether a disclosure is sufficient. Presence is checkable; adequacy is not.
    Producing the deliverable. This checks work; it does not do it.
    Replacing any system of record.
    Automatic action on any finding. Every correction is made by a person.
11. Phasing
    Phase	Content	Complete when
    1	Evidence capture and the checker, arithmetic and structural families only	A large deliverable is checked and every finding traces to a source location
    2	The register with two clocks, manually seeded	A1 and A2 hold
    3	Terms checking against the register	A3 and A4 hold
    4	Assisted terms capture with ratification	Terms are proposed from a document and used only after ratification
    5	The decision loop	A10 and A12 hold
    6	The learning loop	A7, A8 and A9 hold
    7	Coverage and discovery	F8 holds and input reuse rate is being reported

Phases 1 to 3 are the product. Phases 4 to 7 are what make it defensible: detection alone is reproducible by a competent person with a general-purpose tool in about twenty minutes. Knowing what is fine, checking against terms in force on the date, and remembering across periods are not.
