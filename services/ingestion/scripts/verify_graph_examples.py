"""Exercise real partner fixtures without Gmail credentials, Gemini, or DB writes."""
import argparse
import csv
import json
from collections import Counter
from datetime import date
from pathlib import Path

from app.connectors import Item
from app.extraction import Ingestion
from app.graph import Graph
from app.terms import terms_as_of


def read_item(path):
    return Item("fixture", "partner-pack", str(path), path.name, path.read_bytes())


def verify(loader_root, terms_root):
    graph = Graph()
    ingestion = Ingestion(graph)
    ingestion.ingest(read_item(loader_root / "eval/entity_aliases.csv"))
    ingestion.ingest(read_item(terms_root / "stage0_baseline/entity_terms_v1.csv"))
    fund_id = next(e.key for e in graph.state.entities.values() if e.external_ids.get("corvus:legal_entity") == "2254")
    for path, snapshot in (("stage0_baseline/terms_table_v1.csv", "2026-06-30"),
                           ("stage2_email/terms_table_v2.csv", "2026-07-01")):
        Ingestion(graph, fund_id=fund_id, snapshot_as_of=snapshot).ingest(read_item(terms_root / path))
    checked = {}
    for as_of, path in ((date(2026, 6, 30), "stage0_baseline/terms_table_v1.csv"),
                       (date(2026, 9, 30), "stage2_email/terms_table_v2.csv")):
        with (terms_root / path).open() as handle:
            expected = sorted(csv.DictReader(handle), key=lambda row: row["investor_id"])
        actual = terms_as_of(graph, fund_id, as_of)["rows"]
        assert actual == expected, "Terms snapshot differs: " + str(as_of)
        checked[str(as_of)] = len(actual)
    # Exercises stdlib MIME plus the existing PDF parser on the real embedded side letter.
    source = ingestion.ingest(read_item(terms_root / "stage2_email/email_2026-07-06_Trentcombe_amended_side_letter.eml"))
    assert any(edge.predicate == "attached_to" and edge.object == source for edge in graph.state.edges.values())
    before = graph.state.model_dump(mode="json")
    ingestion.ingest(read_item(terms_root / "stage2_email/email_2026-07-06_Trentcombe_amended_side_letter.eml"))
    assert graph.state.model_dump(mode="json") == before, "Replay changed the graph"
    # Verify the same table schema in the XLSX fixtures uses programmatic extraction.
    spreadsheet_graph = Graph()
    Ingestion(spreadsheet_graph).ingest(read_item(terms_root / "stage0_baseline/entity_terms_v1.xlsx"))
    assert any(e.external_ids.get("corvus:legal_entity") == "2254" for e in spreadsheet_graph.state.entities.values())
    assert "facts" not in graph.state.model_dump(mode="json"), "Graph must not store facts"
    return {"status": "passed", "terms_rows_verified": checked,
            "entities": dict(Counter(e.kind for e in graph.state.entities.values())),
            "source_nodes": len(graph.state.sources), "edges": len(graph.state.edges),
            "email_attachment_and_replay": "passed", "xlsx": "passed"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--loader-root", type=Path, required=True)
    parser.add_argument("--terms-root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.loader_root, args.terms_root), indent=2))
