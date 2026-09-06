import json
import os
import unittest
import uuid
from datetime import date
from pathlib import Path
from unittest.mock import patch

from app.connectors import Item
from app.extraction import Ingestion
from app.graph import Graph, key
from app.project_store import ProjectStore, artifact, project_database
from app.projects import all_records, materialize, ratify, snapshot
from app.store import Store
from app.workflows import run_workflow


class CanonicalFixture:
    def __init__(self):
        self.graph, self.blobs, self.documents = Graph(), {}, {}

    def load_graph(self):
        return self.graph

    def save_source_bytes(self, source_id, content):
        self.blobs[source_id] = content

    def get_source_bytes(self, source_id):
        return self.blobs.get(source_id)

    def save(self, document):
        self.documents[document["key"]] = document

    def get(self, document_id):
        return self.documents.get(document_id)


class ProjectValidationTests(unittest.TestCase):
    def test_database_name_cannot_be_injected(self):
        for value in ("documents", "foo; USE DB documents", "a" * 63, "G" * 64):
            with self.assertRaises(ValueError):
                project_database(value)

    def test_original_sources_are_retained_even_on_ingestion_replay(self):
        store = CanonicalFixture()
        item = Item("fixture", "test", "a", "a.txt", b"source bytes")
        source_id = Ingestion(store.graph, store).ingest(item)
        store.blobs.clear()
        Ingestion(store.graph, store).ingest(item)
        self.assertEqual(store.get_source_bytes(source_id), item.content)


@unittest.skipUnless(os.environ.get("KG_PROJECT_TESTS") == "1", "Set KG_PROJECT_TESTS=1 with local SurrealDB and partner fixtures")
class ProjectWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            "SURREAL_PROJECT_SECRET": "local-test-" + uuid.uuid4().hex,
            "SURREAL_PROJECT_ADMIN_USER": os.environ.get("SURREAL_USER", "root"),
            "SURREAL_PROJECT_ADMIN_PASSWORD": os.environ["SURREAL_PASSWORD"],
            "SURREAL_PROJECT_ADMIN_AUTH_LEVEL": os.environ.get("SURREAL_AUTH_LEVEL", "root")})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.created = []
        self.addCleanup(self.clean_databases)
        Store().query("DEFINE NAMESPACE IF NOT EXISTS projects;")
        self.provisioner = "test_provisioner_" + uuid.uuid4().hex
        self.provisioner_password = uuid.uuid4().hex
        Store(namespace="projects", database="catalog").query(
            "DEFINE USER " + self.provisioner + " ON NAMESPACE PASSWORD '" + self.provisioner_password + "' ROLES OWNER;")
        os.environ["SURREAL_PROJECT_ADMIN_USER"] = self.provisioner
        os.environ["SURREAL_PROJECT_ADMIN_PASSWORD"] = self.provisioner_password
        os.environ["SURREAL_PROJECT_ADMIN_AUTH_LEVEL"] = "namespace"
        self.canonical = CanonicalFixture()
        self.root = Path(os.environ["PROJECT_TERMS_FIXTURES"])
        self.sources = {}
        for label, file in (("entity_terms", "stage0_baseline/entity_terms_v1.csv"),
                            ("q2", "stage1_error_injected/q2_2026_fee_and_commitment_schedule_ADMIN_DRAFT.xlsx"),
                            ("q3", "stage2_email/q3_2026_fee_and_commitment_schedule_ADMIN_DRAFT.xlsx"),
                            ("correct", "stage1_error_injected/reference/q2_2026_fee_and_commitment_schedule_CORRECT.xlsx"),
                            ("letter", "stage0_baseline/side_letter_v1_Trentcombe_2024-03-15.md")):
            self.sources[label] = self.ingest(file)
        self.fund = next(e.key for e in self.canonical.graph.state.entities.values() if e.kind == "fund")
        for label, file, day in (("v1", "stage0_baseline/terms_table_v1.csv", "2026-06-30"),
                                 ("v2", "stage2_email/terms_table_v2.csv", "2026-07-01")):
            self.sources[label] = self.ingest(file, fund_id=self.fund, snapshot_as_of=day)
        company = self.canonical.graph.upsert("company", "Test management company " + uuid.uuid4().hex,
                                              self.sources["entity_terms"])
        self.projects = {}
        for quarter in ("2026-Q2", "2026-Q3"):
            project = self.canonical.graph.upsert("project", quarter + " fee review", self.sources["entity_terms"],
                fund_id=self.fund, management_company_id=company, quarter=quarter, workflow_type="fee_run")
            self.projects[quarter] = project
            self.created.append(project)

    def ingest(self, filename, **options):
        file = self.root / filename
        return Ingestion(self.canonical.graph, self.canonical, **options).ingest(
            Item("fixture", "partner", filename, file.name, file.read_bytes()))

    def clean_databases(self):
        for project in self.created:
            Store(namespace="projects", database="catalog").query("REMOVE DATABASE IF EXISTS " + project_database(project) + ";")
        if hasattr(self, "provisioner"):
            Store(namespace="projects", database="catalog").query("REMOVE USER " + self.provisioner + " ON NAMESPACE;")

    def seed(self, quarter):
        selected = [self.sources[label] for label in ("entity_terms", "v1", "v2", "letter", "q2" if quarter == "2026-Q2" else "q3")]
        project = self.projects[quarter]
        result = materialize(self.canonical, project, selected)
        store = ProjectStore(project)
        originals = {sid: a["key"] for a in result["artifacts"] if a["role"] == "original" for sid in a["source_ids"]}
        return store, originals, result

    def test_real_workflows_are_isolated_and_reproducible(self):
        original_graph = self.canonical.graph.state.model_dump(mode="json")
        q2, q2_artifacts, seed = self.seed("2026-Q2")
        q3, q3_artifacts, _ = self.seed("2026-Q3")
        self.assertNotEqual(q2.database, q3.database)
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app)
        route = "/projects/" + q2.project_id
        self.assertEqual(client.get(route + "/graph").status_code, 200)
        download = client.get(route + "/artifacts/" + q2_artifacts[self.sources["q2"]])
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.content, self.canonical.blobs[self.sources["q2"]])
        self.assertEqual(client.get("/projects/" + q3.project_id + "/artifacts/" + q2_artifacts[self.sources["q2"]]).status_code, 404)
        with patch("app.project_api.GraphStore", return_value=self.canonical):
            automatic = client.post(route + "/automate", json={"gate": "terms", "mode": "terms",
                "source_inputs": {"draft": self.sources["q2"], "entity_terms": self.sources["entity_terms"]},
                "evidence_source_ids": [self.sources["v1"], self.sources["v2"], self.sources["letter"]]})
        self.assertEqual(automatic.status_code, 200, automatic.text)
        self.assertEqual(automatic.json()["run"]["status"], "blocked")
        self.assertIn("terms", automatic.json()["inputs"])
        self.assertNotIn("claim_token", automatic.json()["run"])
        self.assertEqual(original_graph, self.canonical.graph.state.model_dump(mode="json"))
        # Actual DB credentials, not just application filtering, reject other DBs.
        for target in ("USE DB " + q3.database, "USE NS markets DB documents"):
            try:
                result = q2.query(target + "; SELECT * FROM node;")
                self.assertEqual(result[-1]["result"], [])
            except RuntimeError:
                pass
            try:
                result = q2.query(target + "; CREATE node:unauthorized SET kind = 'forbidden';")
                self.assertEqual(result[-1]["result"], [])
            except RuntimeError:
                pass
        self.assertIsNone(q3.get_record("node", "unauthorized"))
        self.assertEqual(Store().query("SELECT * FROM node:unauthorized;")[0]["result"], [])
        wrong_project_input = run_workflow(q3, "terms", "arithmetic-only", {"draft": q2_artifacts[self.sources["q2"]]})
        self.assertEqual(wrong_project_input["status"], "blocked", wrong_project_input)
        self.assertIn("project-local", wrong_project_input["output"]["reason"])
        with self.assertRaises(KeyError):
            q3.read_artifact(q2_artifacts[self.sources["q2"]])
        q2_snapshot = snapshot(q2, date(2026, 6, 30))
        q3_snapshot = snapshot(q3, date(2026, 9, 30))
        # Workflows continue after the parent graph and originals are unavailable.
        self.canonical.graph = Graph()
        self.canonical.blobs.clear()
        runs = []
        for store, originals, terms, label, expected in (
            (q2, q2_artifacts, q2_snapshot, "q2", {"TC03"}),
            (q3, q3_artifacts, q3_snapshot, "q3", {"TC01", "TC02", "TC05", "TC09"})):
            inputs = {"draft": originals[self.sources[label]], "terms": terms["key"],
                      "entity_terms": originals[self.sources["entity_terms"]]}
            blocked = run_workflow(store, "terms", "terms", inputs)
            self.assertEqual(blocked["status"], "blocked", blocked)
            approvals = {role: ratify(store, inputs[role], "fixture-reviewer", [self.sources["letter"]],
                                      "Synthetic fixture approval for regression test")["key"]
                         for role in ("terms", "entity_terms")}
            run = run_workflow(store, "terms", "terms", inputs, approvals)
            self.assertEqual(run["status"], "completed", run)
            raw = store.read_artifact(run["output"]["artifacts"]["check_results"])[1]
            checks = json.loads(raw)["checks"]
            self.assertEqual({c["check"] for c in checks if c["status"] == "FAIL"}, expected)
            rerun = run_workflow(store, "terms", "terms", inputs, approvals)
            self.assertEqual(run["key"], rerun["key"])
            self.assertEqual(run["turn"], rerun["turn"])
            self.assertEqual(raw, store.read_artifact(rerun["output"]["artifacts"]["check_results"])[1])
            if label == "q2":
                independent = dict(approvals)
                independent["terms"] = ratify(store, inputs["terms"], "second-fixture-reviewer", [self.sources["letter"]],
                                               "Independent deterministic replay test")["key"]
                fresh = run_workflow(store, "terms", "terms", inputs, independent)
                self.assertEqual(fresh["status"], "completed", fresh)
                self.assertNotEqual(fresh["key"], run["key"])
                self.assertEqual(raw, store.read_artifact(fresh["output"]["artifacts"]["check_results"])[1])
            self.assertFalse(any(node.get("kind") in {"fact", "term_fact"} for node in all_records(store, "node")))
            runs.append(run)
        arithmetic = run_workflow(q2, "terms", "arithmetic-only", {"draft": q2_artifacts[self.sources["q2"]]})
        self.assertEqual(arithmetic["status"], "completed", arithmetic)
        self.assertNotIn("FAIL", arithmetic["output"]["summary"])
        self.assertFalse(arithmetic["output"]["release_ready"])
        self.assertEqual(original_graph["entities"][self.projects["2026-Q2"]]["status"], "in_progress")
        print("\nProject workflow demo: arithmetic=0 failures, Q2=1, Q3=4; DB isolation and offline replay passed")

    def test_wrong_period_and_missing_loader_inputs_are_blocked(self):
        store, originals, _ = self.seed("2026-Q2")
        other = materialize(self.canonical, self.projects["2026-Q2"], [self.sources["q3"]])
        draft = next(a["key"] for a in other["artifacts"] if a["role"] == "original")
        result = run_workflow(store, "terms", "arithmetic-only", {"draft": draft})
        self.assertEqual(result["status"], "blocked", result)
        self.assertIn("quarter", result["output"]["reason"])
        result = run_workflow(store, "loader", "loader", {"draft": originals[self.sources["q2"]]})
        self.assertEqual(result["status"], "blocked", result)
        self.assertIn("mappings", result["output"]["reason"])
        self.assertIn("source_gl", result["output"]["reason"])

    def test_source_recopy_and_corrected_draft_preserve_previous_runs(self):
        store, originals, _ = self.seed("2026-Q2")
        before = list(all_records(store, "artifact"))
        self.seed("2026-Q2")
        self.assertEqual(len(before), len(list(all_records(store, "artifact"))))
        terms = snapshot(store, date(2026, 6, 30))
        inputs = {"draft": originals[self.sources["q2"]], "terms": terms["key"],
                  "entity_terms": originals[self.sources["entity_terms"]]}
        approvals = {role: ratify(store, inputs[role], "fixture-reviewer", [self.sources["letter"]], "Test fixture approval")["key"]
                     for role in ("terms", "entity_terms")}
        first = run_workflow(store, "terms", "terms", inputs, approvals)
        added = materialize(self.canonical, self.projects["2026-Q2"], [self.sources["correct"]])
        inputs["draft"] = next(a["key"] for a in added["artifacts"] if a["role"] == "original")
        second = run_workflow(store, "terms", "terms", inputs, approvals)
        self.assertEqual(second["status"], "completed", second)
        self.assertNotEqual(first["key"], second["key"])
        self.assertEqual(second["turn"], first["turn"] + 1)
        self.assertTrue(second["output"]["release_ready"])
        self.assertEqual(store.get_record("run", first["key"])["output"]["summary"]["FAIL"], 1)


if __name__ == "__main__":
    unittest.main()
