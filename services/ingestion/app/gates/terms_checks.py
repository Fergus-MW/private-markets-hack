"""
Terms-layer checks: does a fee and commitment schedule agree with the terms register as at a date?

Usage:
  uv run --with pandas --with openpyxl python3 terms_checks.py --schedule <schedule.xlsx> --as-of 2026-06-30 \
      [--terms <terms_table.csv>] [--entity-terms <entity_terms.csv>] [--arithmetic-only] \
      --json results.json

Modes
  terms mode (default): needs --terms. Checks the schedule against the register in force on --as-of.
  --arithmetic-only:    no register. Only the checks a schedule can pass on its own (shares, gross fee arithmetic,
                        roll-forward, totals). This is the "no brain" run: it proves footing cannot see a side letter.

States
  PASS      the check holds
  FAIL      the check does not hold (hard)
  WARN      surfaced, not necessarily wrong (soft)
  DECISION  not an error: something the register or the administrator must supply before the check can run
  SKIPPED   not applicable in this mode

Tiers (design rule 1)
  a  changes a balance, an allocation or the scope
  b  changes a report line, or must be resolved before upload
  c  hygiene

Amount at stake is the tier a total only: tier b amounts are components of the same money.

Writes deterministic JSON checks. The project workflow owns artifact retention and audit metadata.
Exit code 1 when any check FAILs.
"""
import argparse, sys, json, hashlib, datetime as dt
from pathlib import Path
import numpy as np, pandas as pd

TIER = {"TC00": "a", "TC01": "b", "TC02": "b", "TC03": "a", "TC04": "a", "TC05": "b", "TC06": "a", "TC07": "b", "TC08": "b", "TC09": "a", "TC10": "a"}
TIER_NAME = {"a": "changes a balance, an allocation or the scope", "b": "changes a report line or must be resolved before upload", "c": "hygiene"}
ARITHMETIC = {"TC06", "TC07", "TC08", "TC10"}
TOL = 0.01

def close(a, b, atol=1e-8):
    return np.isclose(a, b, atol=atol, rtol=0)

ap = argparse.ArgumentParser()
ap.add_argument("--schedule", required=True); ap.add_argument("--as-of", required=True); ap.add_argument("--terms"); ap.add_argument("--entity-terms")
ap.add_argument("--arithmetic-only", action="store_true"); ap.add_argument("--json", required=True)
a = ap.parse_args()
if not a.arithmetic_only and not a.terms: ap.error("--terms is required unless --arithmetic-only is set")
AS_OF = pd.Timestamp(a.as_of); MODE = "arithmetic-only" if a.arithmetic_only else "terms"
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest() if p else None

# ---------------------------------------------------------------- inputs
S = pd.read_excel(a.schedule, sheet_name="Schedule"); cover = pd.read_excel(a.schedule, sheet_name="Cover")
entity_name = str(cover.loc[cover["Item"] == "Entity", "Value"].iloc[0]) if "Item" in cover.columns and (cover["Item"] == "Entity").any() else "?"
tot = S[S["investor_id"] == "TOTAL"].iloc[0]; S = S[S["investor_id"] != "TOTAL"].copy()
T = None; invested = None; entity_id = None
if a.terms:
    T = pd.read_csv(a.terms); T["valid_from"] = pd.to_datetime(T["valid_from"]); T["valid_to"] = pd.to_datetime(T["valid_to"])
    T = T[(T["valid_from"] <= AS_OF) & (T["valid_to"].isna() | (T["valid_to"] >= AS_OF))]
if a.entity_terms:
    E = pd.read_csv(a.entity_terms)
    hit = E[E["term"] == "invested_capital_30jun2026"]; invested = float(hit["value"].iloc[0]) if len(hit) else None
    hit = E[E["term"] == "entity_id_corvus"]; entity_id = str(hit["value"].iloc[0]) if len(hit) else None

R = []
def rep(id_, name, bad, detail_cols, amount=None, state_if_bad="FAIL", frame=None):
    f = S if frame is None else frame; rows = f[bad]
    R.append(dict(check=id_, tier=TIER.get(id_, "c"), status=state_if_bad if len(rows) else "PASS", name=name, investors=", ".join(rows["investor_name"].astype(str)) if len(rows) else "", n=int(len(rows)),
                  amount=(float(amount[bad].sum()) if amount is not None and len(rows) else 0.0),
                  detail="; ".join(f"{r['investor_name']}: " + ", ".join(f"{c}={r[c]}" for c in detail_cols if c in r.index) for _, r in rows.iterrows()) if len(rows) else ""))
def skip(id_, name): R.append(dict(check=id_, tier=TIER.get(id_, "c"), status="SKIPPED", name=name + " [needs the register]", investors="", n=0, amount=0.0, detail=""))

# ---------------------------------------------------------------- arithmetic checks (both modes)
share_sched = S["commitment"].astype(float) / S["commitment"].astype(float).sum()
rep("TC07", "Gross fee = basis amount x rate / 4", ~close(S["gross_fee"], (S["basis_amount"] * S["rate_applied"] / 4).round(2), atol=TOL), ["gross_fee", "basis_amount", "rate_applied"])
fee_in = np.where(S["fee_inside_commitment_applied"] == "Y", S["net_fee"], 0.0)
rf_bad = (~close(S["fee_called_inside"], fee_in, atol=TOL)) | (~close(S["called_end"], S["called_start"] + S["capital_calls_q"] + S["fee_called_inside"], atol=TOL)) | (~close(S["unfunded_end"], S["commitment"] - S["called_end"], atol=TOL))
rep("TC10", "Roll-forward foots inside the schedule (called_end = start + calls + fee inside; unfunded = commitment - called)", rf_bad, ["called_start", "capital_calls_q", "fee_called_inside", "called_end", "unfunded_end"])
foot_bad = {c: round(float(tot[c]) - float(S[c].sum()), 2) for c in ["gross_fee", "offset_amount", "net_fee", "called_end", "unfunded_end"] if abs(float(tot[c]) - float(S[c].sum())) > TOL}
R.append(dict(check="TC08", tier=TIER["TC08"], status="FAIL" if foot_bad else "PASS", name="Totals row foots to the column sums", investors="", n=len(foot_bad), amount=0.0, detail=str(foot_bad)))

# ---------------------------------------------------------------- register checks (terms mode)
if a.arithmetic_only:
    rep("TC06", "Schedule allocation share equals commitment share (schedule-internal)", ~close(S["commitment_share"].astype(float), share_sched, atol=1e-6), ["commitment_share"])
    for cid, nm in [("TC00", "Every schedule investor has a register row in force"), ("TC01", "Rate applied equals the register"), ("TC02", "Fee basis applied equals the register"), ("TC03", "Fee inside or outside commitment as the register says"), ("TC04", "Fee-exempt investors charged nothing"), ("TC05", "Offset percentage equals the register"), ("TC09", "Net fee equals the fee recomputed from the register")]: skip(cid, nm)
    J = S.copy()
else:
    J = S.merge(T, on="investor_id", how="left", suffixes=("", "_t"))
    missing = J["mgmt_fee_rate_pa"].isna()
    rep("TC00", "Every schedule investor has a register row in force on the as-of date", missing, ["investor_id"], state_if_bad="DECISION", frame=J)
    J = J[~missing].copy(); exempt = J["fee_exempt"] == "Y"
    J["basis_expected"] = np.where(J["fee_basis"] == "Commitment", J["commitment"].astype(float), (invested * J["commitment_share"].astype(float)) if invested is not None else np.nan)
    J["gross_expected"] = (J["basis_expected"] * J["mgmt_fee_rate_pa"].astype(float) / 4).round(2); J["offset_expected"] = (J["offsettable_fees_share"] * J["fee_offset_pct"].astype(float).fillna(0)).round(2)
    J["net_fee_expected"] = np.where(exempt, 0.0, (J["gross_expected"] - J["offset_expected"]).round(2)); J["net_fee_overcharge"] = (J["net_fee"] - J["net_fee_expected"]).round(2)
    rep("TC01", "Rate applied equals the rate in the terms register", (~exempt) & (~close(J["rate_applied"].astype(float), J["mgmt_fee_rate_pa"].astype(float))), ["rate_applied", "mgmt_fee_rate_pa", "source_document"], amount=(J["basis_amount"] * (J["rate_applied"] - J["mgmt_fee_rate_pa"]) / 4).abs(), frame=J)
    rep("TC02", "Fee basis applied equals the basis in the terms register", (~exempt) & (J["fee_basis_applied"] != J["fee_basis"]), ["fee_basis_applied", "fee_basis", "basis_amount", "basis_expected", "source_clause"], amount=((J["basis_amount"] - J["basis_expected"]).abs() * J["mgmt_fee_rate_pa"].astype(float) / 4).fillna(0), frame=J)
    called_expected = J["called_start"] + J["capital_calls_q"] + np.where(J["fee_inside_commitment"] == "Y", J["net_fee"], 0.0)
    J["unfunded_expected"] = (J["commitment"] - called_expected).round(2); J["unfunded_overstated_by"] = (J["unfunded_end"] - J["unfunded_expected"]).round(2)
    rep("TC03", "Fee drawn inside or outside commitment as the terms say, and the unfunded roll-forward follows", (J["fee_inside_commitment_applied"] != J["fee_inside_commitment"]) | (~close(J["unfunded_end"], J["unfunded_expected"], atol=TOL)), ["fee_inside_commitment_applied", "fee_inside_commitment", "unfunded_end", "unfunded_expected", "unfunded_overstated_by", "source_clause"], amount=J["unfunded_overstated_by"].abs(), frame=J)
    rep("TC04", "Fee-exempt investors are charged nothing", exempt & (J["gross_fee"].abs() > TOL), ["gross_fee"], amount=J["gross_fee"].abs(), frame=J)
    rep("TC05", "Offset percentage applied equals the terms register", (~exempt) & (~close(J["offset_pct_applied"].astype(float), J["fee_offset_pct"].astype(float))), ["offset_pct_applied", "fee_offset_pct", "source_clause"], amount=(J["offsettable_fees_share"] * (J["offset_pct_applied"] - J["fee_offset_pct"])).abs(), frame=J)
    share_t = J["commitment_t"].astype(float) / J["commitment_t"].astype(float).sum()
    rep("TC06", "Schedule allocation share equals commitment share in the register", ~close(J["commitment_share"].astype(float), share_t, atol=1e-6), ["commitment_share"], frame=J)
    tc09_bad = J["net_fee_expected"].notna() & (J["net_fee_overcharge"].abs() > TOL)
    rep("TC09", "Net fee equals the fee recomputed from the register (headline overcharge in currency)" + ("" if invested is not None else " [Invested Capital rows skipped: pass --entity-terms]"), tc09_bad, ["net_fee", "net_fee_expected", "net_fee_overcharge"], amount=J["net_fee_overcharge"].abs(), frame=J)

# ---------------------------------------------------------------- report
order = {"TC00": 0, "TC01": 1, "TC02": 2, "TC03": 3, "TC04": 4, "TC05": 5, "TC06": 6, "TC07": 7, "TC08": 8, "TC09": 9, "TC10": 10}
df = pd.DataFrame(R).sort_values("check", key=lambda s: s.map(order)).reset_index(drop=True)
pd.set_option("display.width", 230); pd.set_option("display.max_colwidth", 120)
summary = df["status"].value_counts().to_dict(); by_tier = df[df["status"].isin(["FAIL", "WARN"])].groupby("tier").size().to_dict()
hdr = f"Mode: {MODE} | entity: {entity_name} | as-of: {a.as_of} | schedule: {Path(a.schedule).name}" + (f" | register: {Path(a.terms).name} ({len(T)} rows, {', '.join(sorted(T['version'].astype(str).unique())) if 'version' in T else 'no version label'})" if T is not None else " | register: none")
print(hdr); print(df[["check", "tier", "status", "name", "investors", "amount"]].to_string(index=False))
for _, r in df[df["status"].isin(["FAIL", "DECISION"])].iterrows(): print(f"\n  {r['check']} [{r['status']}, tier {r['tier']}] {r['detail']}")
# Tier b findings are components of the tier a lines, so only tier a is added up.
at_stake = round(float(df.loc[(df["status"] == "FAIL") & (df["tier"] == "a"), "amount"].sum()), 2)
print("\nSUMMARY:", summary, "| findings by tier:", by_tier, "| amount at stake (tier a):", at_stake)

# Only deterministic checker output belongs here. The workflow owns audit time.
Path(a.json).write_text(json.dumps({"summary": summary, "findings_by_tier": by_tier,
                                  "amount_at_stake": at_stake, "mode": MODE, "as_of": a.as_of,
                                  "entity": entity_name, "entity_id": entity_id,
                                  "terms_rows_in_force": int(len(T)) if T is not None else 0,
                                  "checks": df.to_dict("records")}, sort_keys=True, default=str))
sys.exit(1 if "FAIL" in summary else 0)
