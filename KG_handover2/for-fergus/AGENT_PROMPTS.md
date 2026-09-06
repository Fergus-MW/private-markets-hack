# Paste-ready prompts for your Claude Code session

Each prompt is self-contained and names its acceptance test. Run them from the repository root on the merged branch.

## B1: fixtures into the live graph, contract test against the live endpoint

```
The graph service runs locally via compose.yaml (ingestion on http://127.0.0.1:18080). Load four structured fixtures into the live graph through the Drive connector (POST /connectors/sync, provider drive; the Drive folder is shared with the connector account and named "ylookup-fixtures"), in this order: entity_aliases.csv and entity_terms_v1.csv with no extra fields; then terms_table_v1.csv with fund_id set to the canonical key of the fund whose external id corvus:legal_entity is 2254 (find it with GET /graph/entities?kind=fund) and snapshot_as_of 2026-06-30; then terms_table_v2.csv with the same fund_id and snapshot_as_of 2026-07-01. Then save GET /graph/funds/<key>/terms?as_of=2026-06-30 to /tmp/live_v1.json and ?as_of=2026-09-30 to /tmp/live_v2.json and run:
  uv run --with pandas python3 KG_handover2/built-tonight/eval/contract_test.py --export /tmp/live_v1.json --fixture KG_handover/fixtures/terms_table_v1.csv
  uv run --with pandas python3 KG_handover2/built-tonight/eval/contract_test.py --export /tmp/live_v2.json --fixture KG_handover/fixtures/terms_table_v2.csv
Acceptance: both print "equal". If not, list the differing cells and stop; do not change the checker or the fixtures. Do not send email or modify Drive files; the connector is read-only.
```

## D3: the connect-screen link (only if agreed)

```
In frontend/index.html add <a class="gate-link" href="/gate/">Open the QC gate</a> immediately after the <p id="auth-status" …></p> element, and in frontend/src/style.css add
.gate-link { display: inline-block; margin-top: 22px; font-size: 13px; font-weight: 550; color: #f5f1ec; opacity: .72; text-decoration: none; } .gate-link:hover { opacity: 1; }
after the .auth-status:empty rule. Change nothing else. Acceptance: npm run dev in frontend, http://localhost:5173/ shows the link under the Google button, http://localhost:5173/gate/ lists five pages, npm test passes.
```

## Optional: a provenance endpoint for one finding

```
Add GET /graph/provenance to services/ingestion/app/graph_api.py. Input: source_id (a source node key) or fact reference (fund_id, investor_id, field, as_of). Output: the chain for that fact as JSON: the email source (sender, received_at, message id), the attached document source, the investment_account rows closed and added around the as_of (old value, new value, clause where the row carries it), and the terms snapshot hash for that fund and date. Read-only; no new tables; reuse Source nodes and attached_to / received_via / invests_in edges. Acceptance: for fund key <key>, investor_id 7335_02891, field mgmt_fee_rate_pa, as_of 2026-09-30 the response shows 0.0085 closed at 2026-06-30 and 0.0075 valid from 2026-07-01 with the side-letter source; unit test added beside tests/test_graph.py; existing tests still pass.
```

## Verifying KG's half without touching it

```
Run KG_handover2/built-tonight/VERIFY.sh and report the last ten lines. Expected: two "equal" lines, run B summary {'PASS': 10, 'FAIL': 1} with TC03 at 22,149.55, run C2 summary {'PASS': 7, 'FAIL': 4} with TC09 at 9,296.43, and three files written in verify_out (an HTML page, an xlsx with a QC gate sheet, a six-line notification). Do not modify anything under KG_handover2.
```
