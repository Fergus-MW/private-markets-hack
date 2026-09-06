"""Term-amendment proposals from correspondence (PRD F3.1 to F3.4).

An email or letter can propose a change to an investor's terms. Each proposal is bound
to the exact line it came from and is unusable until a named person ratifies it.
Ratifying appends a new investment_account row to the fund's register carrying two
clocks: valid_from (in force in the world) and snapshot_as_of (the day it was ratified,
the "known since" clock). The prior row is never edited; terms_as_of prefers the later
snapshot at the same valid_from, and a known_at earlier than the ratification still
returns the old term.

Deterministic: no model runs here. Message text is evidence, never instruction; the
only thing read from it is a small set of labelled term lines, and the investor is
resolved from identity the register already holds (the notices email), never from a
display name alone.
"""
import json
import re
from datetime import date

from app.graph import Source, key, now
from app.terms import terms_as_of

FIELDS = {"mgmt_fee_rate_pa": "rate", "fee_offset_pct": "rate", "fee_basis": "text", "cas_deadline_days": "int"}
PATTERNS = [
    ("mgmt_fee_rate_pa", re.compile(r"management fee rate:\s*([0-9]+(?:\.[0-9]+)?)\s*percent per annum(?:,\s*previously\s*([0-9]+(?:\.[0-9]+)?)\s*percent)?", re.I)),
    ("fee_offset_pct", re.compile(r"fee offset[^:\n]*:\s*([0-9]+(?:\.[0-9]+)?)\s*percent(?:,\s*previously\s*([0-9]+(?:\.[0-9]+)?)\s*percent)?", re.I)),
    ("fee_basis", re.compile(r"fee basis:\s*(?:our share of\s*)?(invested capital|commitment)(?:\s+rather than\s+(commitment|invested capital))?", re.I)),
    ("cas_deadline_days", re.compile(r"capital account statement[^0-9\n]*within\s*([0-9]+)\s*days(?:[^0-9\n]*previously\s*([0-9]+)\s*days)?", re.I)),
]
CLAUSE = re.compile(r"\(clause\s*([0-9]+(?:\([a-z]\))?)\)", re.I)
EFFECTIVE = re.compile(r"(?:from|effective)\s+([0-9]{1,2}\s+[A-Z][a-z]+\s+[0-9]{4})", re.I)
MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"], 1)}


def normalise(field, raw):
    if raw is None:
        return None
    kind = FIELDS[field]
    if kind == "rate":
        return f"{round(float(raw) / 100, 6):g}"
    if kind == "int":
        return str(int(raw))
    return " ".join(word.capitalize() for word in raw.split())


def parse_effective(text):
    match = EFFECTIVE.search(text)
    if not match:
        return None
    day, month, year = match.group(1).split()
    try:
        return date(int(year), MONTHS[month.lower()], int(day)).isoformat()
    except (KeyError, ValueError):
        return None


def term_lines(text):
    """Labelled term lines and what they say. Pure function of the text."""
    found = []
    for line in text.splitlines():
        for field, pattern in PATTERNS:
            match = pattern.search(line)
            if not match:
                continue
            clause = CLAUSE.search(line)
            found.append({"field": field, "new": normalise(field, match.group(1)),
                          "old_stated": normalise(field, match.group(2)) if match.lastindex and match.lastindex >= 2 else None,
                          "clause": clause.group(1) if clause else None, "effective_stated": parse_effective(line),
                          "quote": line.strip()})
    return found


def register_rows(graph, fund_id, as_of):
    try:
        return terms_as_of(graph, fund_id, as_of)["rows"]
    except (ValueError, KeyError):
        return []


def resolve_investors(rows, sender, text):
    """The notices email in the register wins; otherwise an investor whose registered
    name appears verbatim in the text. Never a bare display name from the message."""
    sender = (sender or "").casefold().strip()
    if sender:
        hits = [row for row in rows if (row.get("notices_email") or "").casefold().strip() == sender]
        if hits:
            return hits, "notices_email"
    lowered = text.casefold()
    hits = [row for row in rows if row.get("investor_name") and row["investor_name"].casefold() in lowered]
    return hits, "investor_name in text"


def candidate_funds(graph, source_ids, sender, text=""):
    """Funds whose register knows the sender's notices email, whose registered investor
    name appears verbatim in the text, or that the message is recorded as mentioning."""
    funds = set()
    sender = (sender or "").casefold().strip()
    lowered = text.casefold()
    for source in graph.state.sources.values():
        if source.metadata.get("record_type") != "investment_account":
            continue
        row = json.loads(source.text)
        if (sender and (row.get("notices_email") or "").casefold().strip() == sender) or \
                (row.get("investor_name") and row["investor_name"].casefold() in lowered):
            funds.add(graph.resolve(source.metadata["fund_id"]))
    for edge in graph.state.edges.values():
        if edge.subject in source_ids and edge.predicate == "mentions":
            target = graph.state.entities.get(edge.object)
            if target and target.kind == "fund" and not target.merged_into:
                funds.add(graph.resolve(edge.object))
    return sorted(funds)


def message_text(graph, source_id):
    ids = {source_id} | {edge.subject for edge in graph.state.edges.values()
                         if edge.predicate == "attached_to" and edge.object == source_id and edge.subject in graph.state.sources}
    return ids, "\n".join(graph.state.sources[i].text for i in sorted(ids))


def propose_for_source(graph, source_id, sender=None, as_of=None):
    """Attach proposals to a message source. Idempotent: the same message yields the same
    proposal ids, and an existing ratified proposal is never reset to proposed."""
    source = graph.state.sources[source_id]
    sender = sender or source.metadata.get("sender")
    as_of = as_of or date.today()
    ids, text = message_text(graph, source_id)
    lines = term_lines(text)
    existing = {p["proposal_id"]: p for p in source.metadata.get("term_proposals", [])}
    proposals = []
    for fund_id in candidate_funds(graph, ids, sender, text):
        rows = register_rows(graph, fund_id, as_of)
        investors, matched_by = resolve_investors(rows, sender, text)
        for row in investors:
            for line in lines:
                if row.get(line["field"]) is None:
                    continue
                effective = line["effective_stated"] or row.get("valid_from")
                proposal_id = key("term-proposal", source_id, fund_id, row["investor_id"], line["field"], line["new"], effective)[:16]
                if proposal_id in existing:
                    proposals.append(existing[proposal_id])
                    continue
                proposals.append({"proposal_id": proposal_id, "fund_id": fund_id, "investor_id": row["investor_id"],
                                  "investor_name": row["investor_name"], "investor_matched_by": matched_by,
                                  "field": line["field"], "old": row.get(line["field"]), "old_stated": line["old_stated"],
                                  "new": line["new"], "clause": line["clause"], "effective_from": effective,
                                  "effective_source": "stated in the message" if line["effective_stated"] else "inherited from the row in force",
                                  "quote": line["quote"], "source_id": source_id, "sender": sender,
                                  "received_at": source.recorded_at, "status": "proposed" if str(row.get(line["field"])) != str(line["new"]) else "no_change",
                                  "ratified_by": None, "ratified_at": None, "reason": None, "applied_source_id": None})
    source.metadata["term_proposals"] = proposals
    return proposals


def list_proposals(graph, status=None):
    out = []
    for source in graph.state.sources.values():
        for proposal in source.metadata.get("term_proposals", []):
            if status is None or proposal["status"] == status:
                out.append(proposal)
    return sorted(out, key=lambda p: (p["received_at"], p["proposal_id"]))


def ratify(graph, source_id, proposal_id, actor, reason, recorded_on=None):
    """A named person makes a proposal a term. Appends a register row; never edits the prior one."""
    if not actor or not reason:
        raise ValueError("Ratification needs a named actor and a reason")
    source = graph.state.sources.get(source_id)
    if not source:
        raise KeyError("Unknown message source")
    proposal = next((p for p in source.metadata.get("term_proposals", []) if p["proposal_id"] == proposal_id), None)
    if not proposal:
        raise KeyError("Unknown proposal")
    if proposal["status"] != "proposed":
        raise ValueError("Proposal is " + proposal["status"] + ", not open for ratification")
    if proposal["quote"] not in message_text(graph, source_id)[1]:
        raise ValueError("Proposal quote is no longer present in the message")
    recorded_on = recorded_on or date.today().isoformat()
    fund_id = graph.resolve(proposal["fund_id"])
    effective = date.fromisoformat(proposal["effective_from"])
    base = next((row for row in register_rows(graph, fund_id, max(effective, date.fromisoformat(recorded_on)))
                 if row["investor_id"] == proposal["investor_id"]), None)
    if base is None:
        raise ValueError("No register row in force for the investor on the effective date")
    row = dict(base)
    row[proposal["field"]] = proposal["new"]
    row["valid_from"] = proposal["effective_from"]
    row["valid_to"] = ""
    attachment = next((graph.state.sources[i].filename for i in sorted(message_text(graph, source_id)[0]) if i != source_id), None)
    row["source_document"] = (attachment or (source.metadata.get("subject") or "Correspondence") + " (" + source.filename + ")") + ", ratified " + recorded_on
    row["source_clause"] = proposal["clause"] or base.get("source_clause") or "-"
    if "version" in row:
        row["version"] = (base.get("version") or "v") + "+" + proposal_id[:6]
    # Same-day re-ratification for the same investor replaces that day's row rather than
    # creating an equal-ranked sibling terms_as_of would reject.
    same_day = [s for s in graph.state.sources.values() if s.metadata.get("record_type") == "investment_account"
                and graph.resolve(s.metadata["fund_id"]) == fund_id and s.external_id == proposal["investor_id"]
                and s.metadata.get("snapshot_as_of") == recorded_on and s.metadata.get("ratification")]
    account_id = same_day[0].key if same_day else key("ratified-term", fund_id, proposal["investor_id"], source_id, proposal_id)
    history = same_day[0].metadata.get("ratification_history", []) if same_day else []
    ratification = {"proposal_id": proposal_id, "actor": actor, "reason": reason, "at": now(), "evidence_source_id": source_id,
                    "field": proposal["field"], "old": proposal["old"], "new": proposal["new"], "clause": proposal["clause"]}
    graph.state.sources[account_id] = Source(
        key=account_id, kind="record", provider=source.provider, account=source.account,
        external_id=proposal["investor_id"], revision=proposal_id, filename=source.filename,
        sha256=key(row), text=json.dumps(row),
        metadata={"record_type": "investment_account", "fund_id": fund_id, "investor_name": row["investor_name"],
                  "source_id": source_id, "snapshot_as_of": recorded_on, "ratification": ratification,
                  "ratification_history": history + ([same_day[0].metadata["ratification"]] if same_day else [])})
    graph.edge(account_id, "invests_in", fund_id, source_id, valid_from=row["valid_from"] or None, valid_to=None)
    graph.edge(account_id, "part_of", source_id, source_id)
    proposal.update(status="ratified", ratified_by=actor, ratified_at=ratification["at"], reason=reason, applied_source_id=account_id)
    return {"proposal": proposal, "applied_source_id": account_id, "row": row}
