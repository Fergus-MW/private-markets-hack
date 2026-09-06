"""
Render a gate run as the checklist-with-evidence page (build plan section 4b). Static HTML, opens from a file, no server.

  uv run --with pandas python3 render_results.py --run <run.json> --out <page.html>
      [--compare <run.json>]            a second run of the same draft to show side by side on the scoreboard (the "no brain" run)
      [--delta <terms_delta_v1_to_v2.json>] [--triples <triples_v2_delta.csv>] [--email <email.md>]
                                        provenance for the failing checks: what changed, when it took effect, when it became known, via what
      [--runs-dir <dir>]                earlier runs of the same draft become the turn history
      [--eval-set "cases 51 + 11 · mutation score 100% (4 mutants)"]
      [--graph-url <url>]               link target for "open in graph"

Accepts run.json from terms_checks.py or eval_loader.py.
"""
import argparse, json, html, csv, re
from pathlib import Path
from datetime import datetime

ap = argparse.ArgumentParser(); ap.add_argument("--run", required=True); ap.add_argument("--out", required=True); ap.add_argument("--compare"); ap.add_argument("--delta"); ap.add_argument("--triples"); ap.add_argument("--email"); ap.add_argument("--runs-dir"); ap.add_argument("--eval-set", default=""); ap.add_argument("--graph-url", default="")
a = ap.parse_args()
TIER_NAME = {"a": "changes a balance, an allocation or the scope", "b": "changes a report line or must be resolved before upload", "c": "hygiene"}
FIELD_OF = {"TC01": ["mgmt_fee_rate_pa"], "TC02": ["fee_basis"], "TC05": ["fee_offset_pct"], "TC09": ["mgmt_fee_rate_pa", "fee_basis", "fee_offset_pct"], "TC03": ["fee_inside_commitment"]}
LABEL = {"mgmt_fee_rate_pa": "rate", "fee_basis": "basis", "fee_offset_pct": "offset", "fee_inside_commitment": "fee inside commitment", "cas_deadline_days": "statement deadline (days)", "notices_contact": "notices contact", "notices_email": "notices email"}
def esc(x): return html.escape("" if x is None else str(x))
def fmt_pct(v):
    try: return f"{float(v)*100:.2f}%"
    except Exception: return str(v)

def load_run(p):
    r = json.loads(Path(p).read_text()); checks = []
    for c in r["checks"]:
        if "check" in c and "name" in c: checks.append(dict(id=c["check"], tier=c["tier"], status=c["status"], name=c["name"], investors=c.get("investors", ""), amount=float(c.get("amount", 0) or 0), detail=c.get("detail", "")))
        else: checks.append(dict(id=c["id"], tier=c["tier"], status=c["status"], name=c["check"], investors="", amount=0.0, detail=f"observed {c['observed']}; expected {c['expected']}"))
    r["_checks"] = checks; r["_draft"] = Path(r.get("schedule_file") or r.get("candidate") or "?").name; r["_draft_sha"] = r.get("schedule_sha256") or r.get("candidate_sha256") or ""
    r["_terms_sha"] = r.get("terms_snapshot_sha256") or r.get("mapping_tables_sha256") or ""; r["_mode"] = r.get("mode", r.get("gate")); return r
R = load_run(a.run); C = load_run(a.compare) if a.compare else None
delta = json.loads(Path(a.delta).read_text()) if a.delta else None
triples = list(csv.DictReader(open(a.triples))) if a.triples else []
sender = ""
if a.email:
    m = re.search(r"\*\*From:\*\*\s*(.+)", Path(a.email).read_text()); sender = m.group(1).strip() if m else ""
received_via = next((t["object"] for t in triples if t["predicate"] == "received_via"), "")
supersedes = next((t["object"] for t in triples if t["predicate"] == "supersedes"), "")

# turn history: earlier runs of the same draft
history = []
if a.runs_dir:
    for rp in sorted(Path(a.runs_dir).glob("*/run.json")):
        try: rr = json.loads(rp.read_text())
        except Exception: continue
        if (rr.get("schedule_sha256") or rr.get("candidate_sha256")) == R["_draft_sha"] and rr.get("mode", rr.get("gate")) == R["_mode"]:
            history.append((rr["run_at"], rr["run_id"], sum(1 for c in rr["checks"] if c["status"] == "FAIL")))
turn = next((i + 1 for i, h in enumerate(history) if h[1] == R["run_id"]), len(history) or 1)

def counts(r):
    ch = r["_checks"]; f = [c for c in ch if c["status"] == "FAIL"]
    return dict(a=sum(c["tier"] == "a" for c in f), b=sum(c["tier"] == "b" for c in f), c=sum(c["tier"] == "c" for c in f), warn=sum(c["status"] == "WARN" for c in ch), dec=sum(c["status"] == "DECISION" for c in ch),
                passes=sum(c["status"] == "PASS" for c in ch), run=sum(c["status"] != "SKIPPED" for c in ch), amount=sum(c["amount"] for c in f if c["tier"] == "a"), fails=len(f))   # amount at stake = tier a only (tier b lines are components of it); build plan 4b
K = counts(R); KC = counts(C) if C else None

def provenance(check):
    """Why the register says what it says: from the delta, the triples and the email."""
    if not delta or not check["investors"] or delta.get("investor_name") not in check["investors"]: return ""
    fields = FIELD_OF.get(check["id"], []); ch = {c["field"]: c for c in delta["changes"]}
    bits = []
    for f in fields:
        if f in ch: c = ch[f]; new = fmt_pct(c["new"]) if f in ("mgmt_fee_rate_pa", "fee_offset_pct") else c["new"]; old = fmt_pct(c["old"]) if f in ("mgmt_fee_rate_pa", "fee_offset_pct") else c["old"]; bits.append(f"<b>{esc(LABEL.get(f, f))}</b> {esc(new)} <span class=\"muted\">(was {esc(old)}, clause {esc(c['clause'])})</span>")
        elif f == "fee_inside_commitment": bits.append("<b>fee inside commitment</b> unchanged since the 2024 letter <span class=\"muted\">(clause 2(b), continued by clause 2(d))</span>")
    src = ch.get(fields[0], {}) if fields else {}
    lines = [f"<div class=\"prov-row\">because: " + " · ".join(bits) + "</div>" if bits else "",
             f"<div class=\"prov-row\">source: <b>{esc(delta['source_document'])}</b></div>",
             f"<div class=\"prov-row\">in force from <b>{esc(delta['effective'])}</b> · known since <b>{esc(delta['received'])}</b> · via {esc(delta['delivered_by'])}" + (f" from {esc(sender)}" if sender else "") + (f" <span class=\"mono muted\">{esc(received_via)}</span>" if received_via else "") + "</div>",
             (f"<div class=\"prov-row\">supersedes <span class=\"mono\">{esc(supersedes)}</span></div>" if supersedes else "")]
    link = f"<a class=\"btn\" href=\"{esc(a.graph_url)}\">open in graph</a>" if a.graph_url else ""
    return "<div class=\"prov\">" + "".join(lines) + link + "</div>"

def check_row(c):
    st = c["status"]; glyph = {"PASS": ("✓", "g-pass"), "FAIL": ("✗", f"g-{c['tier']}"), "WARN": ("!", "g-warn"), "DECISION": ("?", "g-d"), "SKIPPED": ("–", "g-skip")}[st]
    amt = f"<span class=\"amt\">USD {c['amount']:,.2f}</span>" if c["amount"] else ""
    ev = ""
    if st in ("FAIL", "WARN", "DECISION") and c["detail"]:
        ev = f"<details open><summary>evidence</summary><div class=\"ev\">{esc(c['detail'])}</div>{provenance(c)}" + ("" if st == "PASS" else "<div class=\"decide\"><label><input type=\"radio\" name=\"d-" + esc(c['id']) + "\"> fix draft</label> <label><input type=\"radio\" name=\"d-" + esc(c['id']) + "\"> accept with reason <input type=\"text\" placeholder=\"reason\"></label> <label><input type=\"radio\" name=\"d-" + esc(c['id']) + "\"> escalate</label></div>") + "</details>"
    return f"<div class=\"chk\"><span class=\"glyph {glyph[1]}\">{glyph[0]}</span><span class=\"cid mono\">{esc(c['id'])}</span><span class=\"cname\">{esc(c['name'])}</span><span class=\"who\">{esc(c['investors'])}</span>{amt}</div>{ev}"

def section(title, sub, items):
    if not items: return ""
    return f"<section><h2>{esc(title)} <span class=\"sub\">{esc(sub)}</span></h2>" + "".join(check_row(c) for c in items) + "</section>"
ch = R["_checks"]
body = ""
for t in ["a", "b", "c"]: body += section(f"Tier {t}", TIER_NAME[t], [c for c in ch if c["status"] in ("FAIL", "WARN") and c["tier"] == t])
body += section("Decisions owed", "not errors: something the administrator must supply", [c for c in ch if c["status"] == "DECISION"])
body += section("Passes", "shown, not hidden", [c for c in ch if c["status"] == "PASS"])
skipped = [c for c in ch if c["status"] == "SKIPPED"]
if skipped: body += section("Not run in this mode", "needs the register", skipped)

def scoreboard(label, k, mode):
    return f"""<div class="score"><div class="score-h">{esc(label)} <span class="muted mono">{esc(mode)}</span></div>
      <div class="pills"><span class="pill p-a">tier a {k['a']}</span><span class="pill p-b">tier b {k['b']}</span><span class="pill p-c">tier c {k['c']}</span><span class="pill p-d">decisions {k['dec']}</span><span class="pill p-pass">passes {k['passes']} of {k['run']}</span></div>
      <div class="big">{k['fails']}<span class="big-l">errors caught</span></div><div class="amt-l">amount at stake (tier a) USD {k['amount']:,.2f}</div></div>"""
sb = (scoreboard("No brain", KC, C["_mode"]) if C else "") + scoreboard("Brain on" if C else "This run", K, R["_mode"])
hist = " → ".join(f"turn {i+1}: {h[2]} findings <span class=\"mono muted\">({h[1][-6:]})</span>" for i, h in enumerate(history)) if history else "first run of this draft"
page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Gate: {esc(R['_draft'])}</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Serif:wght@600&family=IBM+Plex+Sans:wght@400;600&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
:root{{--paper:#F3F5F7;--surface:#fff;--ink:#1B222D;--muted:#5A6472;--line:#D6DCE3;--accent:#454B8F;--accent-soft:#E7E8F5;--pass:#2C7A4B;--pass-bg:#E3F2E8;--a:#A9362B;--a-bg:#F8E4E1;--b:#A8701B;--b-bg:#F9EED8;--c:#6E7785;--c-bg:#E9ECF0;--d:#2A66B0;--d-bg:#E1ECF9;--warn:#A8701B;--warn-bg:#F9EED8}}
@media (prefers-color-scheme: dark){{:root:not([data-theme="light"]){{--paper:#13171D;--surface:#1B2027;--ink:#E4E8EE;--muted:#9AA4B2;--line:#2B333D;--accent:#A3A8EA;--accent-soft:#262B4A;--pass:#63C48F;--pass-bg:#163124;--a:#F0897C;--a-bg:#3A1F1B;--b:#E6B25E;--b-bg:#3A2E16;--c:#A6AFBB;--c-bg:#262C35;--d:#83B4F2;--d-bg:#1B2C44;--warn:#E6B25E;--warn-bg:#3A2E16}}}}
:root[data-theme="dark"]{{--paper:#13171D;--surface:#1B2027;--ink:#E4E8EE;--muted:#9AA4B2;--line:#2B333D;--accent:#A3A8EA;--accent-soft:#262B4A;--pass:#63C48F;--pass-bg:#163124;--a:#F0897C;--a-bg:#3A1F1B;--b:#E6B25E;--b-bg:#3A2E16;--c:#A6AFBB;--c-bg:#262C35;--d:#83B4F2;--d-bg:#1B2C44;--warn:#E6B25E;--warn-bg:#3A2E16}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--paper);color:var(--ink);font:15px/1.5 "IBM Plex Sans",system-ui,sans-serif}} .wrap{{max-width:1080px;margin:0 auto;padding:28px 22px 80px}}
.mono{{font-family:"IBM Plex Mono",monospace;font-size:.9em}} .muted{{color:var(--muted)}} h1{{font:600 26px/1.2 "IBM Plex Serif",Georgia,serif;margin:0 0 6px}} h2{{font:600 17px/1.3 "IBM Plex Serif",Georgia,serif;margin:26px 0 10px;padding-top:12px;border-top:1px solid var(--line)}} h2 .sub{{font:400 13px "IBM Plex Sans",sans-serif;color:var(--muted);margin-left:8px}}
.head{{display:grid;grid-template-columns:1fr 1fr;gap:6px 24px;font-size:13.5px;background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:14px 16px}} .head b{{font-weight:600}}
.scores{{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px;margin:16px 0}} .score{{background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:14px 16px}} .score-h{{font-weight:600;margin-bottom:8px}}
.pills{{display:flex;flex-wrap:wrap;gap:6px}} .pill{{font-size:12px;font-weight:600;padding:2px 9px;border-radius:999px}} .p-a{{background:var(--a-bg);color:var(--a)}} .p-b{{background:var(--b-bg);color:var(--b)}} .p-c{{background:var(--c-bg);color:var(--c)}} .p-d{{background:var(--d-bg);color:var(--d)}} .p-pass{{background:var(--pass-bg);color:var(--pass)}}
.big{{font:600 44px/1 "IBM Plex Serif",Georgia,serif;margin:12px 0 2px;font-variant-numeric:tabular-nums}} .big-l{{font:400 13px "IBM Plex Sans",sans-serif;color:var(--muted);margin-left:10px}} .amt-l{{font-size:13px;color:var(--muted)}}
.chk{{display:grid;grid-template-columns:28px 64px 1fr auto auto;gap:10px;align-items:center;padding:8px 10px;background:var(--surface);border:1px solid var(--line);border-radius:6px;margin-top:6px}} .cid{{color:var(--accent)}} .who{{font-size:13px;color:var(--muted)}} .amt{{font-variant-numeric:tabular-nums;font-weight:600}}
.glyph{{display:inline-grid;place-items:center;width:24px;height:24px;border-radius:6px;font-family:"IBM Plex Mono",monospace;font-weight:500}} .g-pass{{background:var(--pass-bg);color:var(--pass)}} .g-a{{background:var(--a-bg);color:var(--a)}} .g-b{{background:var(--b-bg);color:var(--b)}} .g-c{{background:var(--c-bg);color:var(--c)}} .g-d{{background:var(--d-bg);color:var(--d)}} .g-warn{{background:var(--warn-bg);color:var(--warn)}} .g-skip{{background:var(--c-bg);color:var(--c)}}
details{{margin:0 0 4px 38px;font-size:13.5px}} summary{{cursor:pointer;color:var(--muted)}} .ev{{font-family:"IBM Plex Mono",monospace;font-size:12.5px;white-space:pre-wrap;background:var(--accent-soft);border-radius:6px;padding:8px 10px;margin:6px 0}}
.prov{{border-left:3px solid var(--accent);padding:6px 12px;margin:6px 0;background:var(--surface)}} .prov-row{{margin:2px 0}} .btn{{display:inline-block;margin-top:6px;font-size:12.5px;font-weight:600;color:var(--accent);background:var(--accent-soft);padding:3px 10px;border-radius:5px;text-decoration:none}}
.decide{{margin:6px 0 8px;font-size:13px;color:var(--muted)}} .decide label{{margin-right:14px}} .decide input[type=text]{{font:inherit;border:1px solid var(--line);border-radius:4px;padding:1px 6px;background:var(--surface);color:var(--ink)}}
.strip{{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:26px;font-size:13.5px}} .strip>div{{background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:12px 14px}} .strip b{{display:block;font-size:11.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin-bottom:6px}}
@media (max-width:640px){{.head,.strip{{grid-template-columns:1fr}} .chk{{grid-template-columns:28px 64px 1fr}} .who,.amt{{grid-column:3}}}}
</style></head><body><div class="wrap">
<h1>Gate result: {esc(R['_draft'])}</h1>
<div class="head">
  <div><b>Draft</b> <span class="mono">{esc(R['_draft'])}</span> · hash <span class="mono">{esc(R['_draft_sha'][:8])}…</span></div>
  <div><b>Entity</b> {esc(R.get('entity') or R.get('entities_loaded', '') and str(R.get('entities_loaded')) + ' entities')} · <b>As-of</b> {esc(R.get('as_of', '—'))}</div>
  <div><b>Terms snapshot</b> {esc(Path(R.get('terms_file') or '').name or ('none: ' + R['_mode'] if R.get('gate') == 'terms_checks' else 'mapping tables'))} · hash <span class="mono">{esc((R['_terms_sha'] or '')[:8])}{'…' if R['_terms_sha'] else '—'}</span>{(' · ' + str(R.get('terms_rows_in_force')) + ' facts in force') if R.get('terms_rows_in_force') else ''}</div>
  <div><b>Run</b> <span class="mono">{esc(R['run_id'])}</span> at {esc(R['run_at'][:16].replace('T', ' '))} · <b>Turn {turn}</b> of this draft</div>
</div>
<div class="scores">{sb}</div>
{body}
<div class="strip"><div><b>History</b>{hist} · decisions recorded: 0</div><div><b>Eval set</b>{esc(a.eval_set) or 'not stated'} · new cases from this run: 0</div></div>
<p class="muted" style="font-size:12.5px;margin-top:22px">Static page rendered by render_results.py from <span class="mono">{esc(Path(a.run).name)}</span>. Decision controls are recorded by the gate in the next build (K9); on this page they are visual only.</p>
</div></body></html>"""
Path(a.out).write_text(page); print("written", a.out, f"({len(page.encode()):,} bytes)", "| fails:", K["fails"], "| compare:", (KC["fails"] if KC else "—"), "| turn", turn, "of", len(history) or 1)
