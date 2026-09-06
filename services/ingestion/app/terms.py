"""Deterministic temporal view of structured investor-account terms."""
import json
from datetime import date


def terms_as_of(graph, fund_id, as_of, known_at=None):
    # recorded_at is an ISO string, so accept either form of the same instant
    # rather than compare a date against it and raise.
    if known_at is not None and not isinstance(known_at, str):
        known_at = known_at.isoformat()
    fund_id = graph.resolve(fund_id)
    selected = {}
    for source in graph.state.sources.values():
        if source.metadata.get("record_type") != "investment_account":
            continue
        if graph.resolve(source.metadata["fund_id"]) != fund_id:
            continue
        if known_at and source.recorded_at > known_at:
            continue
        row = json.loads(source.text)
        snapshot = source.metadata.get("snapshot_as_of") or ""
        if snapshot and date.fromisoformat(snapshot) > as_of:
            continue
        start, end = row.get("valid_from"), row.get("valid_to")
        if (start and date.fromisoformat(start) > as_of) or (end and date.fromisoformat(end) < as_of):
            continue
        investor = row["investor_id"]
        previous = selected.get(investor)
        rank = (start or "", snapshot)
        if not previous or rank > previous[2]:
            selected[investor] = (row, [source.key], rank)
        elif rank == previous[2]:
            if row != previous[0]:
                raise ValueError("Conflicting terms with the same effective date for investor " + investor)
            previous[1].append(source.key)
    ordered = sorted(selected)
    return {"fund_id": fund_id, "as_of": as_of.isoformat(),
            "rows": [selected[investor][0] for investor in ordered],
            "provenance": {investor: selected[investor][1] for investor in ordered}}
