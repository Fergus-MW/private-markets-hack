"""Run Fergus's terms_as_of (his code, read-only import) over KG's fixtures and export the contract-shaped CSVs."""
import sys, csv, json
from pathlib import Path
from datetime import date
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "services" / "ingestion"))   # the repository root, wherever it is cloned
from app.connectors import Item
from app.extraction import Ingestion
from app.graph import Graph
from app.terms import terms_as_of
L02 = Path(__file__).resolve().parents[1] / "stage0_baseline"   # entity_aliases.csv sits here in the handover
L05 = Path(__file__).resolve().parents[1]
OUT = Path(__file__).resolve().parent
def item(p): return Item("fixture", "partner-pack", str(p), p.name, p.read_bytes())
g = Graph(); ing = Ingestion(g)
ing.ingest(item(L02 / "entity_aliases.csv")); ing.ingest(item(L05 / "stage0_baseline/entity_terms_v1.csv"))
fund = next(e.key for e in g.state.entities.values() if e.external_ids.get("corvus:legal_entity") == "2254")
for p, snap in (("stage0_baseline/terms_table_v1.csv", "2026-06-30"), ("stage2_email/terms_table_v2.csv", "2026-07-01")):
    Ingestion(g, fund_id=fund, snapshot_as_of=snap).ingest(item(L05 / p))
for d in ("2026-06-30", "2026-09-30"):
    r = terms_as_of(g, fund, date.fromisoformat(d)); rows = r["rows"]; cols = list(rows[0].keys())
    with open(OUT / f"terms_as_of_{d}.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rows)
    (OUT / f"terms_as_of_{d}.json").write_text(json.dumps(r, indent=1, default=str))
    print(d, len(rows), "rows exported; fund key =", fund, "; sample rate for 7335_02891 =", next(x["mgmt_fee_rate_pa"] for x in rows if x["investor_id"] == "7335_02891"))
