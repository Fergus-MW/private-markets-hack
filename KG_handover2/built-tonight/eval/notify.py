"""
The notification surface of the gate (build plan 4b, surface 3 of 3): a short plain-text message from the run record for chat or email.
It carries the scoreboard and a link; decisions are never made here.

  python3 eval/notify.py --run results/runC2_q3_after_email.json [--page-url <url>] [--out results/notification_q3.txt]
"""
import argparse, json
from pathlib import Path
ap = argparse.ArgumentParser(); ap.add_argument("--run", required=True); ap.add_argument("--page-url", default=""); ap.add_argument("--out"); a = ap.parse_args()
run = json.loads(Path(a.run).read_text()); ch = run["checks"]; fails = [c for c in ch if c["status"] == "FAIL"]; dec = [c for c in ch if c["status"] == "DECISION"]
tier = lambda t: sum(1 for c in fails if c["tier"] == t); passes = sum(1 for c in ch if c["status"] == "PASS"); skipped = sum(1 for c in ch if c["status"] == "SKIPPED")
at_stake = sum(float(c.get("amount") or 0) for c in fails if c["tier"] == "a")
top = max(fails, key=lambda c: ({"a": 0, "b": 1, "c": 2}.get(c["tier"], 3) * -1, float(c.get("amount") or 0))) if fails else None
lines = [f"QC gate · {Path(run['schedule_file']).name} · {run['entity']} · as-of {run['as_of']}",
         f"Tier a {tier('a')} · Tier b {tier('b')} · Tier c {tier('c')} · decisions owed {len(dec)} · passes {passes} of {len(ch)}" + (f" · skipped {skipped} (no register)" if skipped else ""),
         f"Amount at stake (tier a): USD {at_stake:,.2f}" if fails else "Nothing found. Every applicable check passed."]
if top: lines.append(f"Top finding: {top['check']} (tier {top['tier']}) {top['name']} · {top['investors']} · USD {float(top.get('amount') or 0):,.2f}")
lines.append(f"Terms: {Path(str(run['terms_file'])).name if run.get('terms_file') else 'none'} · run {run['run_id']}")
if a.page_url: lines.append(f"Open the page to decide: {a.page_url}")
msg = "\n".join(lines); print(msg)
if a.out: Path(a.out).write_text(msg + "\n")
