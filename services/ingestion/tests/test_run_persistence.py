import unittest

from app.project_store import ProjectStore
from app.workflow_agents import findings, persist


class Store:
    """Minimal stand-in: bundle() is the only durability boundary persist() uses."""
    def __init__(self):
        self.nodes, self.artifacts, self.links = [], [], []

    def bundle(self, nodes=(), artifacts=(), links=(), decisions=()):
        self.nodes += list(nodes)
        self.artifacts += list(artifacts)
        self.links += list(links)


class PersistTests(unittest.TestCase):
    def test_failed_run_still_records_its_partial_findings(self):
        store = Store()
        records = findings("run", "qc", [{"topic": "missing", "detail": "terms snapshot"},
                                         {"topic": "failure", "detail": "ValueError: unknown artifact"}])
        persist(store, "run", "qc", "failed", "could not complete", {"Plan": "a1"}, records)
        run = next(node for node in store.nodes if node["kind"] == "run")
        self.assertEqual(run["status"], "failed")
        self.assertEqual({n["topic"] for n in store.nodes if n["kind"] == "finding"}, {"missing", "failure"})
        # The partial plan artifact stays reachable from the failed run.
        self.assertIn(("run", "produced", "a1"), [(l["subject"], l["predicate"], l["object"]) for l in store.links])

    def test_report_artifact_is_always_written_and_linked(self):
        store = Store()
        persist(store, "run", "explain", "completed", "all good", {}, [])
        self.assertEqual([item["filename"] for item in store.artifacts], ["agent-report.md"])
        self.assertEqual(len([l for l in store.links if l["predicate"] == "produced"]), 1)


class Reader(ProjectStore):
    """Exercises the real nodes_of_kind gating without a database."""
    def __init__(self):
        pass

    def query(self, sql, variables=None):
        self.sql, self.variables = sql, variables or {}
        return [{"result": []}]


class ExplanationTests(unittest.TestCase):
    def test_explanations_are_a_separate_kind_from_findings(self):
        rows = findings("run", "explain", [{"topic": "explanation", "detail": "QC found two issues"}],
                        kind="explanation")
        self.assertEqual(rows[0]["kind"], "explanation")

    def test_reads_are_scoped_to_one_kind_so_commentary_never_enters_evidence(self):
        reader = Reader()
        for kind in ("finding", "check_result", "explanation"):
            reader.nodes_of_kind(kind)
            self.assertEqual(reader.variables["kind"], kind)
            self.assertIn("kind = $kind", reader.sql)
        # Anything outside the allowlist, including the whole node table, is refused.
        for kind in ("node", "source", "artifact"):
            with self.assertRaises(ValueError):
                reader.nodes_of_kind(kind)

    def test_finding_keys_are_unchanged_by_the_kind_parameter(self):
        item = [{"topic": "rule", "detail": "Fee is 1%"}]
        self.assertNotEqual(findings("run", "first-run", item)[0]["key"],
                            findings("run", "first-run", item, kind="explanation")[0]["key"])


if __name__ == "__main__":
    unittest.main()
