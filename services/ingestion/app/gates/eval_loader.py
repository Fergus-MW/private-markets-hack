"""
Numerical eval set for a GL -> Corvus loader build (dataset 02).
Runs deterministic pass/fail checks on a candidate loader workbook against the source GL
and the mapping tables. No answer key needed for HARD checks; the optional KEY block
compares to a reference loader when one exists.

Usage:
  uv run --with pandas --with openpyxl python3 eval_loader.py <candidate.xlsx> [--sheet "Upload Template"] [--key <reference.xlsx>] [--out results.md]
"""
import sys, argparse, json, re, hashlib, datetime as dt
import numpy as np, pandas as pd
from pathlib import Path

# Tiers (design rule 1): a changes a balance, an allocation or the scope; b changes a report line or must be resolved before upload; c hygiene.
TIER = {**{k: "b" for k in ["F01", "F02", "F03", "F04", "F05", "F06", "T06", "B04", "B05", "C04", "R01", "R02", "R04", "R05", "R06", "R07", "R08", "R09", "R10", "R11", "R12", "R13", "R14", "R15", "DEC01"]},
        **{k: "a" for k in ["T01", "T02", "T03", "T04", "T05", "B01", "B02", "B03", "B06", "C01", "C03", "R03", "D01", "A01", "S01", "S02", "S03", "K01", "K02", "K03"]},
        **{k: "c" for k in ["C02", "D02", "D03", "A02", "A03", "A04", "A05", "S04"]}}
TIER_NAME = {"a": "changes a balance, an allocation or the scope", "b": "changes a report line or must be resolved before upload", "c": "hygiene"}

TOL = 0.005

ap = argparse.ArgumentParser(); ap.add_argument("candidate"); ap.add_argument("--sheet", default="Upload Template"); ap.add_argument("--key"); ap.add_argument("--key-sheet", default="Upload Template (VERIFIED v4c)"); ap.add_argument("--json", required=True)
ap.add_argument("--source", required=True); ap.add_argument("--mappings", required=True)
ap.add_argument("--quarter-start", required=True); ap.add_argument("--quarter-end", required=True)
a = ap.parse_args()
SRC, MAPS = Path(a.source), Path(a.mappings)
Q_START, Q_END = a.quarter_start, a.quarter_end
R = []   # (id, tier, group, severity, status, observed, expected, note)
def chk(id_, group, sev, ok, observed, expected, note=""):
    """sev: hard -> FAIL when not ok; soft -> WARN (surfaced, not necessarily wrong); decision -> DECISION (not an error: someone must decide)."""
    status = "PASS" if ok else {"soft": "WARN", "decision": "DECISION"}.get(sev, "FAIL")
    R.append((id_, TIER.get(id_, "c"), group, sev, status, observed, expected, note))

print("loading…", flush=True)
gl = pd.read_excel(SRC, sheet_name="Investor-Level GL"); gl["Source Row"] = gl["Original Source Row"] if "Original Source Row" in gl else gl.index + 2
L = pd.read_excel(a.candidate, sheet_name=a.sheet); L = L.drop(columns=[c for c in L.columns if str(c).startswith("Unnamed")])
le_map = pd.read_excel(MAPS, sheet_name="LE Mapping", header=1); ent = pd.read_excel(MAPS, sheet_name="Entity Listing"); coa = pd.read_excel(MAPS, sheet_name="CoA Mapping")
corvus = pd.read_excel(MAPS, sheet_name="Corvus CoA"); bpref = pd.read_excel(MAPS, sheet_name="Batch Preference"); inv_map = pd.read_excel(MAPS, sheet_name="Investor Mapping"); deal_map = pd.read_excel(MAPS, sheet_name="Deal Mapping")

# ---------------------------------------------------------------- 0. shape / format
EXPECTED_COLS = ["Batch Index", "JE Index", "Transaction Index", "Legal Entity", "Legal Entity ID", "GL Date", "Effective Date", "Deal Name", "Deal ID", "Position", "Position ID", "Trans Type", "Transaction Currency", "Investor Amount (Local)", "Is Debit", "Investor Amount (LE)", "Batch Type", "Batch Comments", "Transaction Comments", "Allocation Rule", "Investor Account ID", "Vehicle", "Bank Account", "UDF Lookup", "UDF Text", "Supplier", "Investor Quantity"]
chk("F01", "format", "hard", list(L.columns[:27]) == EXPECTED_COLS, list(L.columns[:27]) == EXPECTED_COLS, True, "27 target columns in Phase I order")
chk("F02", "format", "hard", "Batch ref" in L.columns, "Batch ref" in L.columns, True, "source Batch ID carried for traceability")
chk("F03", "format", "hard", set(L["Is Debit"].unique()) <= {"Y", "N"}, sorted(L["Is Debit"].unique()), ["N", "Y"], "Is Debit carries only Y or N")
for c in ["Investor Amount (Local)", "Investor Amount (LE)"]:
    x = pd.to_numeric(L[c], errors="coerce"); chk("F04" if "Local" in c else "F05", "format", "hard", x.notna().all() and (x >= 0).all(), f"nulls={x.isna().sum()} negatives={(x<0).sum()}", "0 / 0", f"{c} unsigned, non-null")
req = ["Legal Entity ID", "GL Date", "Effective Date", "Deal ID", "Trans Type", "Transaction Currency", "Batch Type", "Allocation Rule", "Investor Account ID", "Vehicle"]
nulls = {c: int(L[c].isna().sum()) for c in req if L[c].isna().any()}; chk("F06", "format", "hard", not nulls, nulls, {}, "mandatory columns non-null")

# ---------------------------------------------------------------- 1. tie-out to source (needs Batch ref)
if "Batch ref" in L.columns:
    src = gl.copy(); src["absL"] = src["Amount (Local Currency)"].abs().round(2); src["absE"] = src["Amount (Entity Currency)"].abs().round(2)
    src["side"] = np.where(src["Debits (Entity Currency)"] > 0, "Y", np.where(src["Credits (Entity Currency)"] > 0, "N", "Z"))
    L2 = L.copy(); L2["absL"] = L2["Investor Amount (Local)"].round(2); L2["absE"] = L2["Investor Amount (LE)"].round(2)
    # investor key: source (Legal Entity, RFX ID) -> Corvus Specific Id (unique in Investor Mapping); loader already carries the Specific Id
    sid = inv_map.drop_duplicates(["Legal Entity", "Specific External Ref 1"]).set_index(["Legal Entity", "Specific External Ref 1"])["Corvus Specific Id"]
    src["SID"] = [sid.get((le, r)) for le, r in zip(src["Legal Entity"], src["RFX ID"])]
    src["JE"] = pd.to_numeric(src["Journal Entry Index"]); src["TX"] = pd.to_numeric(src["Transaction Index"]); L2["JE"] = pd.to_numeric(L2["JE Index"]); L2["TX"] = pd.to_numeric(L2["Transaction Index"])
    s_k = src.groupby(["Batch ID", "JE", "TX", "SID", "absL", "absE", "side"]).size().rename("n_src"); l_k = L2.groupby(["Batch ref", "JE", "TX", "Investor Account ID", "absL", "absE", "Is Debit"]).size().rename("n_ldr"); l_k.index.names = s_k.index.names
    j = pd.concat([s_k, l_k], axis=1).fillna(0)
    scope_les = set(L["Legal Entity"]); in_scope = src[src["Legal Entity"].isin(scope_les)]
    nonzero = in_scope[(in_scope["absL"] > 0) | (in_scope["absE"] > 0)]
    chk("T01", "tie-out", "hard", len(L) == len(nonzero), len(L), len(nonzero), "loader rows = in-scope source rows with a non-zero amount in either currency")
    missing = j[(j["n_src"] > j["n_ldr"])]; missing = missing[missing.index.get_level_values(0).isin(set(in_scope["Batch ID"]))]
    extra = j[j["n_ldr"] > j["n_src"]]
    chk("T02", "tie-out", "hard", int(extra["n_ldr"].sum() - extra["n_src"].sum()) == 0, int(extra["n_ldr"].sum() - extra["n_src"].sum()), 0, "loader rows with no matching source row (batch/JE/txn/investor/|amounts|/side)")
    zero_src = in_scope[(in_scope["absL"] == 0) & (in_scope["absE"] == 0)]
    miss_nonzero = int(missing["n_src"].sum() - missing["n_ldr"].sum()) - len(zero_src)
    chk("T03", "tie-out", "hard", miss_nonzero <= 0, int(missing["n_src"].sum() - missing["n_ldr"].sum()), len(zero_src), "in-scope source rows not in loader (should equal the zero-amount rows only)")
    for le, g in in_scope.groupby("Legal Entity"):
        pass
    ct = in_scope.groupby("Legal Entity").agg(dr=("Debits (Entity Currency)", "sum"), cr=("Credits (Entity Currency)", "sum"))
    L2["dr"] = np.where(L2["Is Debit"] == "Y", L2["Investor Amount (LE)"], 0.0); L2["cr"] = np.where(L2["Is Debit"] == "Y", 0.0, L2["Investor Amount (LE)"])
    lt = L2.groupby("Legal Entity").agg(dr=("dr", "sum"), cr=("cr", "sum")); d = (ct - lt).abs().max().max()
    chk("T04", "tie-out", "hard", d < TOL, round(float(d), 4), f"< {TOL}", "per-entity gross debits and credits (entity ccy) = source")
    ct2 = in_scope.groupby("Legal Entity").agg(dr=("Debits (Local Currency)", "sum"), cr=("Credits (Local Currency)", "sum")); L2["drl"] = np.where(L2["Is Debit"] == "Y", L2["Investor Amount (Local)"], 0.0); L2["crl"] = np.where(L2["Is Debit"] == "Y", 0.0, L2["Investor Amount (Local)"])
    d2 = (ct2 - L2.groupby("Legal Entity").agg(dr=("drl", "sum"), cr=("crl", "sum"))).abs().max().max(); chk("T05", "tie-out", "hard", d2 < TOL, round(float(d2), 4), f"< {TOL}", "per-entity gross debits and credits (local ccy) = source")
    zq = zero_src["Quantity"].fillna(0).ne(0).sum(); chk("T06", "tie-out", "hard", zq == 0, int(zq), 0, "excluded zero-amount rows carry no quantity")

# ---------------------------------------------------------------- 2. double-entry balance
L["dr"] = np.where(L["Is Debit"] == "Y", L["Investor Amount (LE)"], 0.0); L["cr"] = np.where(L["Is Debit"] == "Y", 0.0, L["Investor Amount (LE)"])
L["drl"] = np.where(L["Is Debit"] == "Y", L["Investor Amount (Local)"], 0.0); L["crl"] = np.where(L["Is Debit"] == "Y", 0.0, L["Investor Amount (Local)"])
def unbalanced(keys, d, c): g = L.groupby(keys).agg(d=(d, "sum"), c=(c, "sum")); return int(((g["d"] - g["c"]).abs() > TOL).sum()), len(g)
u, n = unbalanced(["Batch Index"], "dr", "cr"); chk("B01", "balance", "hard", u == 0, f"{u} of {n}", 0, "every batch balances in entity ccy")
u, n = unbalanced(["Batch Index", "JE Index"], "dr", "cr"); chk("B02", "balance", "hard", u == 0, f"{u} of {n}", 0, "every journal entry balances in entity ccy")
u, n = unbalanced(["Batch Index", "Transaction Currency"], "drl", "crl"); chk("B03", "balance", "hard", u == 0, f"{u} of {n}", 0, "every batch balances per transaction currency in local ccy")
u, n = unbalanced(["Batch Index", "Investor Account ID"], "dr", "cr"); chk("B04", "balance", "soft", u == 0, f"{u} of {n}", 0, "every investor balances within each batch (investor-level double entry)")
chk("B05", "balance", "hard", L.groupby("Batch ref")["Batch Index"].nunique().max() == 1 if "Batch ref" in L.columns else True, "1:1", "1:1", "one Batch Index per source batch")
chk("B06", "balance", "hard", L.groupby("Batch Index")["Legal Entity ID"].nunique().max() == 1, int(L.groupby("Batch Index")["Legal Entity ID"].nunique().max()), 1, "a batch never spans two entities")

# ---------------------------------------------------------------- 3. currency
lec = le_map.set_index("Corvus LE ID")["Corvus Currency"]; L["le_ccy"] = L["Legal Entity ID"].map(lec)
same = L["Transaction Currency"] == L["le_ccy"]; bad = same & ((L["Investor Amount (Local)"] - L["Investor Amount (LE)"]).abs() > TOL)
chk("C01", "currency", "hard", bad.sum() == 0, int(bad.sum()), 0, "same-currency rows: local amount = entity amount")
x = L[~same & (L["Investor Amount (Local)"] > 0.5)].copy(); x["rate"] = x["Investor Amount (LE)"] / x["Investor Amount (Local)"]
sp = x.groupby(["Batch Index", "Transaction Currency"])["rate"].agg(["min", "max"]); wide = int(((sp["max"] / sp["min"] - 1) > 0.02).sum())
chk("C02", "currency", "soft", wide == 0, wide, 0, "implied FX rate within a batch varies by >2% (amounts >0.50 only)")
chk("C03", "currency", "hard", L["le_ccy"].notna().all(), int(L["le_ccy"].isna().sum()), 0, "every Legal Entity ID is in LE Mapping")
mc = corvus.drop_duplicates("Trans Type").set_index("Trans Type")["Multi-Currency / LE Currency Only"]; v = ((L["Trans Type"].map(mc) == "LE Currency Only") & ~same).sum()
chk("C04", "currency", "soft", v == 0, int(v), 0, "rows on an 'LE Currency Only' trans type in a foreign currency (Phase I had 5,358 such rows, so soft)")

# ---------------------------------------------------------------- 4. reference integrity
chk("R01", "reference", "hard", L["Trans Type"].isin(set(corvus["Trans Type"])).all(), sorted(set(L["Trans Type"]) - set(corvus["Trans Type"])), [], "every Trans Type exists in Corvus CoA")
chk("R02", "reference", "hard", L["Investor Account ID"].isin(set(inv_map["Corvus Specific Id"])).all(), int((~L["Investor Account ID"].isin(set(inv_map["Corvus Specific Id"]))).sum()), 0, "every Investor Account ID exists in Investor Mapping")
imle = inv_map.drop_duplicates("Corvus Specific Id").set_index("Corvus Specific Id")["Legal Entity"]; ok = (L["Investor Account ID"].map(imle) == L["Legal Entity"]).all()
chk("R03", "reference", "hard", ok, int((L["Investor Account ID"].map(imle) != L["Legal Entity"]).sum()), 0, "investor account belongs to the row's legal entity")
chk("R04", "reference", "hard", L["Deal ID"].isin(set(deal_map["Corvus Deal ID"])).all(), int((~L["Deal ID"].isin(set(deal_map["Corvus Deal ID"]))).sum()), 0, "every Deal ID exists in Deal Mapping")
pid = L["Position ID"].dropna(); chk("R05", "reference", "hard", pid.isin(set(deal_map["Corvus Position ID"].dropna())).all(), int((~pid.isin(set(deal_map["Corvus Position ID"].dropna()))).sum()), 0, "every Position ID exists in Deal Mapping")
dc = deal_map.drop_duplicates("Corvus Deal ID").set_index("Corvus Deal ID")["Currency2"]; chk("R06", "reference", "soft", (L["Deal ID"].map(dc) == L["Transaction Currency"]).all(), int((L["Deal ID"].map(dc) != L["Transaction Currency"]).sum()), 0, "transaction currency = deal currency")
dreq = corvus.drop_duplicates("Trans Type").set_index("Trans Type")["Deal"]; chk("R07", "reference", "hard", ((L["Trans Type"].map(dreq) == "Required") & L["Deal ID"].isna()).sum() == 0, int(((L["Trans Type"].map(dreq) == "Required") & L["Deal ID"].isna()).sum()), 0, "Deal present where Corvus says Required")
mand = set(coa[coa["MANDATORY SUPPLIER?"] == "YES"]["Verado II TransType (Default)"]); chk("R08", "reference", "hard", (L["Trans Type"].isin(mand) & L["Supplier"].isna()).sum() == 0, int((L["Trans Type"].isin(mand) & L["Supplier"].isna()).sum()), 0, "Supplier present on mandatory-supplier trans types")
qn = corvus.drop_duplicates("Trans Type").set_index("Trans Type")["Quantity"]; chk("R09", "reference", "hard", (L["Investor Quantity"].notna() & (L["Trans Type"].map(qn) == "N/A")).sum() == 0, int((L["Investor Quantity"].notna() & (L["Trans Type"].map(qn) == "N/A")).sum()), 0, "no quantity on trans types where Corvus says N/A")
cash_tt = {"Cash FX gains / losses", "Cash received", "Cash paid"}; b1 = (L["Trans Type"].isin(cash_tt) & L["Bank Account"].isna()).sum(); b2 = (~L["Trans Type"].isin(cash_tt) & L["Bank Account"].notna()).sum()
chk("R10", "reference", "hard", b1 == 0 and b2 == 0, f"cash rows without bank account={int(b1)}, non-cash rows with bank account={int(b2)}", "0 / 0", "Bank Account on cash trans types only (Phase I convention)")
ba_ok = L["Bank Account"].dropna().map(lambda s: bool(re.fullmatch(r"\d+ - (Income|Investment)", str(s)))).all(); chk("R11", "reference", "hard", ba_ok, ba_ok, True, "Bank Account = '<LE ID> - Income|Investment'")
ba_le = L[L["Bank Account"].notna()]; chk("R12", "reference", "hard", (ba_le["Bank Account"].str.split(" - ").str[0].astype(int) == ba_le["Legal Entity ID"]).all(), True, True, "Bank Account LE ID = row's Legal Entity ID")
chk("R13", "reference", "hard", L["Allocation Rule"].nunique() == 1, L["Allocation Rule"].unique().tolist(), ["Eastbury Trentbeck"], "single investor-level allocation rule")
chk("R14", "reference", "hard", L["Batch Type"].isin(set(bpref["Batch Type"])).all(), sorted(set(L["Batch Type"]) - set(bpref["Batch Type"])), [], "every Batch Type is in Batch Preference")
chk("R15", "reference", "hard", L.groupby("Batch Index")["Batch Type"].nunique().max() == 1, int(L.groupby("Batch Index")["Batch Type"].nunique().max()), 1, "one Batch Type per batch")

# ---------------------------------------------------------------- 5. dates
gd = pd.to_datetime(L["GL Date"]); ed = pd.to_datetime(L["Effective Date"])
chk("D01", "dates", "hard", ((gd >= Q_START) & (gd <= Q_END)).all(), f"{gd.min().date()}..{gd.max().date()}", f"{Q_START}..{Q_END}", "GL Date inside the quarter")
chk("D02", "dates", "soft", (ed <= gd).all(), int((ed > gd).sum()), 0, "Effective Date not after GL Date")
chk("D03", "dates", "soft", ((ed < Q_START) & (L["Batch Type"] != "Partner Transfer")).sum() == 0, int(((ed < Q_START) & (L["Batch Type"] != "Partner Transfer")).sum()), 0, "non-transfer rows with Effective Date before the quarter")

# ---------------------------------------------------------------- 6. duplicates / anomalies
k = L.groupby(["Batch Index", "JE Index", "Transaction Index", "Investor Account ID"]).size(); chk("A01", "anomaly", "soft", (k > 1).sum() == 0, int((k > 1).sum()), 0, "same investor twice on one journal line (source artefact; must be surfaced)")
side = corvus.drop_duplicates("Trans Type").set_index("Trans Type")["Debit / Credit"]; ag = (((L["Is Debit"] == "Y") & (L["Trans Type"].map(side) == "Credit")) | ((L["Is Debit"] == "N") & (L["Trans Type"].map(side) == "Debit"))).mean()
chk("A02", "anomaly", "soft", ag < 0.5, f"{ag*100:.1f}%", "< 50% (Phase I = 41%)", "share of rows posted against the Corvus natural side")
for c in ["Investor Amount (Local)", "Investor Amount (LE)"]:
    x = L[c]; m = int((np.abs(x * 100 - np.round(x * 100)) > 1e-6).sum()); chk("A03" if "Local" in c else "A04", "anomaly", "soft", m == 0, m, 0, f"{c} values with more than 2 decimals (pass-through from source)")
chk("A05", "anomaly", "soft", (L["Investor Quantity"] == 0).sum() == 0, int((L["Investor Quantity"] == 0).sum()), 0, "explicit zero quantities loaded (Phase I loads none)")

# ---------------------------------------------------------------- 7. scope
# Names are matched exactly. The only tolerated variants are the rows of entity_aliases.csv (design rule 4: aliases are declared, not fuzzed).
ALIASES = pd.read_csv(Path(__file__).resolve().parent / "entity_aliases.csv"); alias = dict(zip(ALIASES["listing_name"].str.strip(), ALIASES["source_name"].str.strip()))
listing = ent["Entity"].astype(str).str.strip().map(lambda x: alias.get(x, x))            # Entity Listing names expressed as source names
scope_y = set(listing[ent["Ylookup to complete (Y/N)"] == "Y"]); n_in = set(listing[ent["Ylookup to complete (Y/N)"] == "N"])
loaded = set(L["Legal Entity"].astype(str).str.strip().unique()); src_all = set(gl["Legal Entity"].astype(str).str.strip().unique())
chk("S01", "scope", "hard", not (n_in & loaded), sorted(n_in & loaded), [], "no entity flagged N is loaded")
unlisted = sorted(loaded - set(listing)); chk("S02", "scope", "decision", not unlisted, unlisted, [], "loaded entities not on the Entity Listing (exact name or declared alias): a scope decision is owed, not an error")
chk("S03", "scope", "hard", not ((scope_y & src_all) - loaded), sorted((scope_y & src_all) - loaded), [], "every Y entity with source activity is loaded")
used_alias = sorted(k for k, v in alias.items() if v in loaded); chk("S04", "scope", "soft", not used_alias, [f"{k} = {alias[k]}" for k in used_alias], [], "Entity Listing names that resolve to a loaded entity only through the declared alias table (surfaced so the listing can be corrected)")

# ---------------------------------------------------------------- 7b. decisions owed on the mapping
exact_pairs = {(x, t) for x, t in zip(coa["Helio GL Account"], coa["Helio Trans Type"]) if pd.notna(x)}
insc = gl[gl["Legal Entity"].isin(set(L["Legal Entity"]))]
nomap = insc[[(x, t) not in exact_pairs for x, t in zip(insc["GL Account"], insc["Trans Type"])]]
pairs = nomap.groupby(["GL Account", "Trans Type"]).size()
chk("DEC01", "decision", "decision", len(pairs) == 0, f"{len(pairs)} account/trans-type pairs over {len(nomap)} rows; accounts: {sorted(set(nomap['GL Account']))}", "0",
    "in-scope source rows whose (account, trans type) has no exact row in CoA Mapping: whatever mapping the loader applied is undocumented and needs the administrator's approval")

# ---------------------------------------------------------------- 8. optional: compare to a reference loader
if a.key:
    V = pd.read_excel(a.key, sheet_name=a.key_sheet); V = V.drop(columns=[c for c in V.columns if str(c).startswith("Unnamed")])
    chk("K01", "key", "hard", len(L) == len(V), len(L), len(V), "row count = reference")
    kc = ["Batch ref", "JE Index", "Transaction Index", "Investor Account ID", "Trans Type", "Is Debit"]
    if all(c in L.columns for c in kc) and all(c in V.columns for c in kc):
        Lk = L.copy(); Vk = V.copy(); Lk["JE Index"] = pd.to_numeric(Lk["JE Index"]); Lk["Transaction Index"] = pd.to_numeric(Lk["Transaction Index"]); Vk["JE Index"] = pd.to_numeric(Vk["JE Index"]); Vk["Transaction Index"] = pd.to_numeric(Vk["Transaction Index"])
        Lk["occ"] = Lk.groupby(kc).cumcount(); Vk["occ"] = Vk.groupby(kc).cumcount(); m = Lk.merge(Vk, on=kc + ["occ"], how="outer", suffixes=("_c", "_k"), indicator=True)
        chk("K02", "key", "hard", (m["_merge"] == "both").all(), m["_merge"].value_counts().to_dict(), "all both", "row-level key match")
        both = m[m["_merge"] == "both"]; diffs = {}
        for c in [c for c in EXPECTED_COLS if c not in kc and c != "Batch Index"]:
            x, y = both[c + "_c"], both[c + "_k"]
            if c in ("Investor Amount (Local)", "Investor Amount (LE)", "Investor Quantity", "Position ID", "Deal ID", "Legal Entity ID"):
                nd = int((~((pd.to_numeric(x, errors="coerce") == pd.to_numeric(y, errors="coerce")) | (x.isna() & y.isna()))).sum())
            elif c in ("GL Date", "Effective Date"): nd = int((pd.to_datetime(x) != pd.to_datetime(y)).sum())
            else: nd = int((x.astype(object).where(x.notna(), "<NA>").astype(str).str.strip() != y.astype(object).where(y.notna(), "<NA>").astype(str).str.strip()).sum())
            if nd: diffs[c] = nd
        chk("K03", "key", "hard", not diffs, diffs, {}, "exact column equality on matched rows (Batch Index excluded)")

# ---------------------------------------------------------------- report
df = pd.DataFrame(R, columns=["id", "tier", "group", "severity", "status", "observed", "expected", "check"])
summary = df["status"].value_counts().to_dict(); by_tier = df[df["status"].isin(["FAIL", "WARN"])].groupby("tier").size().to_dict()
print(df.to_string(index=False)); print("\nSUMMARY:", summary, "| findings by tier:", by_tier)

# Only deterministic checker output belongs here. The workflow owns audit time.
Path(a.json).write_text(json.dumps({"summary": summary, "findings_by_tier": by_tier,
                                  "checks": df.to_dict("records")}, sort_keys=True, default=str))
sys.exit(1 if "FAIL" in summary else 0)
