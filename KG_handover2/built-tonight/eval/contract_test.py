"""
Contract test for the seam between the graph and the gate.

The graph's export terms_as_of(entity, date) must equal the fixture row for row, column for column.
  uv run --with pandas python3 contract_test.py --export <graph_export.csv> --fixture <terms_table_vN.csv> [--key investor_id]

Columns named in --ignore (default: version) are not compared. Prints "equal" and exits 0, or lists every differing cell (key, column, export value, fixture value) and exits 1.
Numeric cells compare at 1e-9 after parsing; blanks, NaN and "None" are all treated as empty; strings compare after trimming.
"""
import argparse, sys, math
from pathlib import Path
import pandas as pd

ap = argparse.ArgumentParser(); ap.add_argument("--export", required=True); ap.add_argument("--fixture", required=True); ap.add_argument("--key", default="investor_id"); ap.add_argument("--ignore", default="version", help="comma-separated columns not compared (default: version, a table-level label)")
a = ap.parse_args()
import io, csv, json
def load_export(p):
    """A CSV file, or the graph endpoint's JSON response ({rows: [...]}) saved to a file. Values are compared as strings either way."""
    if not str(p).lower().endswith(".json"): return pd.read_csv(p, dtype=str, keep_default_na=False)
    payload = json.loads(Path(p).read_text()); rows = payload.get("rows") if isinstance(payload, dict) else payload
    if not rows: raise SystemExit(f"no rows in {p}")
    return pd.DataFrame(rows, dtype=str).fillna("")
X = load_export(a.export); F = pd.read_csv(a.fixture, dtype=str, keep_default_na=False)
def norm(v):
    s = "" if v is None else str(v).strip()
    if s.lower() in ("", "nan", "none", "null", "nat"): return ""
    try:
        f = float(s.replace(",", "")); return f
    except ValueError: return s
def same(x, y):
    if isinstance(x, float) and isinstance(y, float): return math.isclose(x, y, abs_tol=1e-9, rel_tol=1e-9)
    if isinstance(x, float) or isinstance(y, float): return str(x) == str(y)
    return x == y
problems = []
if list(X.columns) != list(F.columns):
    missing = [c for c in F.columns if c not in X.columns]; extra = [c for c in X.columns if c not in F.columns]
    problems.append(f"columns differ: missing in export {missing}, extra in export {extra}, order {'same' if set(X.columns) == set(F.columns) else 'n/a'}")
if a.key not in X.columns or a.key not in F.columns: print("\n".join(problems) if problems else ""); print(f"key column {a.key!r} missing"); sys.exit(1)
xk, fk = set(X[a.key]), set(F[a.key])
for k in sorted(fk - xk): problems.append(f"row missing in export: {a.key}={k}")
for k in sorted(xk - fk): problems.append(f"row extra in export: {a.key}={k}")
if X[a.key].duplicated().any(): problems.append(f"duplicate keys in export: {sorted(X[a.key][X[a.key].duplicated()])}")
IGN = {c.strip() for c in a.ignore.split(",") if c.strip()}
Xi, Fi = X.set_index(a.key), F.set_index(a.key)
for k in sorted(fk & xk):
    for c in [c for c in F.columns if c != a.key and c in X.columns and c not in IGN]:
        xv, fv = norm(Xi.loc[k, c]), norm(Fi.loc[k, c])
        if not same(xv, fv): problems.append(f"{a.key}={k} column={c}: export={Xi.loc[k, c]!r} fixture={Fi.loc[k, c]!r}")
if problems: print(f"NOT EQUAL: {len(problems)} difference(s) between {a.export} and {a.fixture}"); [print("  " + p) for p in problems[:200]]; sys.exit(1)
print(f"equal: {len(F)} rows x {len(F.columns)} columns (ignoring {sorted(IGN)}), {a.export} == {a.fixture}"); sys.exit(0)
