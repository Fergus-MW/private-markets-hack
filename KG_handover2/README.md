# KG_handover2: overnight build, 5 to 6 September 2026

Two folders, one pull request, read time five minutes.

- **`built-tonight/`**: what exists and runs. Start with `BUILT_TONIGHT.md`, then `./VERIFY.sh` (one minute, needs `uv`). Contents: the contract test passing against the partner's own `terms_as_of` code, the gate reading the register from the graph endpoint, and the three surfaces of one run record (page, workbook with cell comments, notification).
- **`for-fergus/`**: `FOR_FERGUS.md` has the finding from the transcripts, the three-surface pattern, three yes-or-no decisions and two build items with acceptance tests. `AGENT_PROMPTS.md` has paste-ready prompts for each.
- **`frontend/public/gate/`** (in the same PR): the gate result pages served by the existing frontend at `/gate/`, no change to any existing file.

Morning order, 2.5 hours: fixtures into the live graph and the contract test against the live endpoint (B1), merge this PR (D1), then a full rehearsal with every fallback. Fergus builds nothing else today.

Earlier handover, still valid: `../KG_handover/` (ontology, fixtures, integration design, triples). Build plan and runbook live in KG's working folder; the seam in build plan section 5 is unchanged.
