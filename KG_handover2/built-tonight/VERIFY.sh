#!/usr/bin/env bash
# Verifies tonight's build from this folder alone, in about a minute. Needs `uv` (https://docs.astral.sh/uv/) or set UV to any runner
# that provides pandas + openpyxl (e.g. UV="python3 -m" is not enough; just install pandas openpyxl and set UV=env). Writes only into ./verify_out/.
set -uo pipefail; cd "$(dirname "$0")"; rm -rf verify_out; mkdir -p verify_out; UV=${UV:-uv}
run() { $UV run --with pandas --with openpyxl python3 "$@"; }
echo "== 1. Contract test: the partner's terms_as_of export (his code over our fixtures) equals the fixtures, both dates"
run eval/contract_test.py --export graph_export/terms_as_of_2026-06-30.json --fixture stage0_baseline/terms_table_v1.csv
run eval/contract_test.py --export graph_export/terms_as_of_2026-09-30.json --fixture stage2_email/terms_table_v2.csv
echo "== 2. The gate, reading the register in the endpoint's response shape (exit 1 on FAIL is by design)"
run eval/terms_checks.py --terms-json graph_export/terms_as_of_2026-06-30.json --schedule stage1_error_injected/q2_2026_fee_and_commitment_schedule_ADMIN_DRAFT.xlsx --as-of 2026-06-30 --entity-terms stage0_baseline/entity_terms_v1.csv --runs-dir verify_out/runs --json verify_out/runB.json | grep -E "^SUMMARY|TC03 \["
run eval/terms_checks.py --terms-json graph_export/terms_as_of_2026-09-30.json --schedule stage2_email/q3_2026_fee_and_commitment_schedule_ADMIN_DRAFT.xlsx --as-of 2026-09-30 --entity-terms stage0_baseline/entity_terms_v1.csv --runs-dir verify_out/runs --json verify_out/runC2.json | grep -E "^SUMMARY|TC09 \["
echo "== 3. Three surfaces from the one run record"
run eval/render_results.py --run verify_out/runC2.json --delta stage2_email/terms_delta_v1_to_v2.json --runs-dir verify_out/runs --graph-url "http://127.0.0.1:18080/graph/sources" --out verify_out/q3_fee_schedule.html
run eval/gate_to_workbook.py --run verify_out/runC2.json --delta stage2_email/terms_delta_v1_to_v2.json --page-url verify_out/q3_fee_schedule.html --out verify_out/q3_fee_schedule_QC.xlsx
python3 eval/notify.py --run verify_out/runC2.json --page-url verify_out/q3_fee_schedule.html --out verify_out/notification_q3.txt
echo "== 4. The fee terms register: the graph's terms_as_of response as a workbook for the administrator (surface of the graph, not a source)"
run eval/terms_register_xlsx.py --terms-json graph_export/terms_as_of_2026-09-30.json --previous-json graph_export/terms_as_of_2026-06-30.json --entity-terms stage0_baseline/entity_terms_v1.csv --delta stage2_email/terms_delta_v1_to_v2.json --out verify_out/fee_terms_register_2026-09-30.xlsx
echo "== Expected: equal, equal; B = {'PASS': 10, 'FAIL': 1} with TC03 22,149.55; C2 = {'PASS': 7, 'FAIL': 4} with TC09 9,296.43; three files in verify_out/; register: 19 investors, 2 on non-default terms, 9 changed cells"
