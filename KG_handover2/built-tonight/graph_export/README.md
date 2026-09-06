# Graph export, produced with the partner's code (6 Sep 2026, 01:30)

`export_with_partner_code.py` imports the partner's `terms_as_of` from the shared repo clone (read-only), loads our fixtures through his `Ingestion`, and writes `terms_as_of_<date>.json` (his endpoint's response shape: `fund_id`, `as_of`, `rows`, `provenance`) and the same rows as CSV.

Result: `eval/contract_test.py` prints **equal** for 30 Jun 2026 against `terms_table_v1.csv` and for 30 Sep 2026 against `terms_table_v2.csv`. Fund key in his graph for Corvus legal entity 2254: `b8c8dbc67926ab78738dc28d6617b850002c4552c3f1a65c1bc5f699388885b1`.

Use the JSON files with `terms_checks.py --terms-json` as the fallback if his service is down on the day. Re-run:

    uv run --with pydantic --with httpx --with openpyxl python3 graph_export/export_with_partner_code.py
