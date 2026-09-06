import base64
import json
import os
import unittest
from datetime import date
from email.message import EmailMessage
from unittest.mock import patch

import httpx
from pydantic import ValidationError

from app.connectors import GoogleConnector, Item
from app.extraction import Extraction, Ingestion, gemini_extract
from app.graph import Graph, GraphState, IdentityConflict, Source, key
from app.store import GraphStore
from app.terms import terms_as_of


def fixture_source(graph, name="source"):
    source_id = key(name)
    graph.state.sources[source_id] = Source(key=source_id, kind="file", provider="fixture",
        account="test", external_id=name, revision="1", filename=name, sha256=key(name), text=name)
    return source_id


class GraphTests(unittest.TestCase):
    def setUp(self):
        self.graph = Graph()
        self.source = fixture_source(self.graph)

    def test_verified_aliases_merge_but_shared_names_and_domains_do_not(self):
        a = self.graph.upsert("fund", "Dunley II", self.source, external_ids={"corvus:legal_entity": "1703"})
        b = self.graph.upsert("fund", "Dunley 2", self.source, external_ids={"corvus:legal_entity": "1703"})
        self.assertEqual(a, b)
        self.assertEqual(self.graph.state.entities[a].aliases, ["Dunley 2"])
        c = self.graph.upsert("company", "Shared", self.source, domains=["example.com"], external_ids={"registry": "1"})
        d = self.graph.upsert("company", "Shared", self.source, domains=["example.com"], external_ids={"registry": "2"})
        self.assertNotEqual(c, d)

    def test_ambiguous_identifiers_do_not_silently_merge(self):
        self.graph.upsert("person", "A", self.source, emails=["a@example.com"])
        self.graph.upsert("person", "B", self.source, emails=["b@example.com"])
        with self.assertRaises(IdentityConflict):
            self.graph.upsert("person", "Both", self.source, emails=["a@example.com", "b@example.com"])

    def test_project_scope_and_completion_evidence(self):
        fund = self.graph.upsert("fund", "Fund", self.source)
        company = self.graph.upsert("company", "Manager", self.source)
        fields = dict(fund_id=fund, management_company_id=company, quarter="2026-Q2", workflow_type="fee_run")
        q2 = self.graph.upsert("project", "Q2", self.source, **fields)
        self.assertEqual(q2, self.graph.upsert("project", "Renamed", self.source, **fields))
        self.assertNotEqual(q2, self.graph.upsert("project", "Q3", self.source, **{**fields, "quarter": "2026-Q3"}))
        with self.assertRaises(ValidationError):
            self.graph.upsert("project", "Q2", self.source, **fields, status="completed")
        self.graph.upsert("project", "Q2", self.source, **fields, status="completed",
                          completed_at="2026-07-10T09:00:00Z", completion_source_id=self.source)
        self.assertEqual(self.graph.state.entities[q2].status, "completed")

    def test_merge_resolves_edges_and_duplicate_project_scopes(self):
        second_source = fixture_source(self.graph, "second")
        a = self.graph.upsert("fund", "Fund A", self.source)
        b = self.graph.upsert("fund", "Fund Alias", second_source)
        company = self.graph.upsert("company", "Manager", self.source)
        projects = []
        for fund in (a, b):
            p = self.graph.upsert("project", "Q2", self.source, fund_id=fund,
                management_company_id=company, quarter="2026-Q2", workflow_type="fee_run")
            projects.append(p)
            self.graph.edge(p, "for_fund", fund, self.source)
        self.graph.merge(a, b, self.source)
        self.assertEqual(self.graph.resolve(projects[0]), self.graph.resolve(projects[1]))
        flat = self.graph.flatten(b)
        self.assertEqual(flat["entity"]["key"], a)
        self.assertNotIn("facts", flat)
        self.assertTrue(all(e["object"] == a for e in flat["relationships"]))
        GraphState.model_validate(self.graph.state.model_dump(mode="json"))

    def test_source_versions_and_account_scoping(self):
        ingestion = Ingestion(self.graph)
        a = ingestion.ingest(Item("drive", "one", "file", "a.txt", b"old"))
        self.assertEqual(a, ingestion.ingest(Item("drive", "one", "file", "a.txt", b"old")))
        b = ingestion.ingest(Item("drive", "one", "file", "a.txt", b"new"))
        c = ingestion.ingest(Item("drive", "two", "file", "a.txt", b"old"))
        self.assertEqual(len({a, b, c}), 3)
        self.assertEqual(self.graph.state.sources[a].text, "old")

    def test_mime_headers_and_attachment_are_programmatic_and_idempotent(self):
        email = EmailMessage()
        email["From"], email["To"] = "Dana <DANA@example.com>", "Admin <admin@example.com>"
        email.set_content("Please update Q3 terms")
        email.add_attachment(b"amended terms", maintype="text", subtype="plain", filename="letter.txt")
        ingestion = Ingestion(self.graph, parser=lambda *_: self.fail("Parser not needed for MIME/text"))
        item = Item("gmail", "mailbox", "m1", "m1.eml", email.as_bytes(), kind="email")
        source = ingestion.ingest(item)
        count = len(self.graph.state.sources)
        self.assertEqual(source, ingestion.ingest(item))
        self.assertEqual(len(self.graph.state.sources), count)
        self.assertEqual(len([e for e in self.graph.state.edges.values() if e.predicate == "attached_to"]), 1)
        self.assertIn("dana@example.com", [p.emails[0] for p in self.graph.state.entities.values()])
        self.assertFalse(any(e.kind == "project" for e in self.graph.state.entities.values()))

    def test_complex_documents_reuse_parser(self):
        calls = []
        def parser(item, store):
            calls.append(item.filename)
            return "extracted", "document-id", ["OCR used"]
        source = Ingestion(self.graph, parser=parser).ingest(Item("drive", "account", "pdf", "scan.pdf", b"pdf"))
        self.assertEqual(calls, ["scan.pdf"])
        self.assertEqual(self.graph.state.sources[source].document_id, "document-id")

    def test_gemini_is_only_used_for_unstructured_sources_and_is_cached(self):
        result = Extraction.model_validate({"entities": [{"kind": "company", "name": "Manager LLC", "quote": "Manager LLC"}]})
        with patch("app.extraction.gemini_extract", return_value=(result, "gemini-3.1-pro-preview")) as model:
            ingestion = Ingestion(self.graph, use_gemini=True)
            item = Item("drive", "account", "text", "text.txt", b"Manager LLC")
            ingestion.ingest(item)
            ingestion.ingest(item)
            self.assertEqual(model.call_count, 1)
            ingestion.ingest(Item("drive", "account", "csv", "mapping.csv",
                b"kind,name,id_namespace,external_id\nfund,Fund,registry,12\n"))
            self.assertEqual(model.call_count, 1)
        self.assertTrue(any(e.kind == "company" for e in self.graph.state.entities.values()))

    def test_invalid_gemini_output_retains_the_source_for_retry(self):
        item = Item("gmail", "account", "message", "message.txt", b"Manager appears in useful source text")
        # The duplicate name fails after accept_proposals has already inserted
        # its first entity, exercising rollback as well as source retention.
        proposal = Extraction.model_validate({"entities": [
            {"kind": "company", "name": "Manager", "quote": "Manager"},
            {"kind": "company", "name": "Manager", "quote": "Manager"}]})
        with patch("app.extraction.gemini_extract", return_value=(proposal, "gemini-3.1-pro-preview")):
            source_id = Ingestion(self.graph, use_gemini=True).ingest(item)
        source = self.graph.state.sources[source_id]
        self.assertEqual(source.text, "Manager appears in useful source text")
        self.assertNotIn("extraction_complete", source.metadata)
        self.assertTrue(any("source retained for retry" in warning for warning in source.warnings))
        self.assertFalse(any(entity.name == "Manager" for entity in self.graph.state.entities.values()))

    def test_gemini_extraction_uses_gateway_when_configured(self):
        payload = {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text":
            '{"entities":[],"relationships":[],"projects":[]}' }]}}], "modelVersion": "gateway-model"}
        response = httpx.Response(200, request=httpx.Request("POST", "https://gateway.example/v1/generate"), json=payload)
        client = unittest.mock.MagicMock()
        client.__enter__.return_value.post.return_value = response
        with patch.dict(os.environ, {"MODEL_GATEWAY_URL": "https://gateway.example",
                                    "GEMINI_API_KEY": "must-not-be-used"}), \
                patch("app.extraction.fetch_id_token", return_value="identity"), \
                patch("app.extraction.httpx.Client", return_value=client):
            result, version = gemini_extract("Actual source")
        call = client.__enter__.return_value.post.call_args
        self.assertEqual(call.args[0], "https://gateway.example/v1/generate")
        self.assertEqual(call.kwargs["json"]["cache_namespace"], "graph-extraction-v1")
        self.assertEqual(version, "gateway-model")
        self.assertEqual(result.entities, [])

    def test_temporal_terms_preserve_old_and_new_values(self):
        fund = self.graph.upsert("fund", "Fund", self.source)
        ingestion = Ingestion(self.graph, fund_id=fund)
        header = "investor_id,investor_name,source_document,valid_from,valid_to,mgmt_fee_rate_pa\n"
        for version, start, value in (("v1", "2024-03-15", "0.0085"), ("v2", "2026-07-01", "0.0075")):
            ingestion.ingest(Item("drive", "a", version, version + ".csv",
                (header + "7335,Trentcombe,Side letter," + start + ",," + value + "\n").encode()))
        self.assertEqual(terms_as_of(self.graph, fund, date(2026, 6, 30))["rows"][0]["mgmt_fee_rate_pa"], "0.0085")
        self.assertEqual(terms_as_of(self.graph, fund, date(2026, 9, 30))["rows"][0]["mgmt_fee_rate_pa"], "0.0075")

    def test_explicit_relationship_rows_link_by_identifier_without_a_model(self):
        ingestion = Ingestion(self.graph, parser=lambda *_: self.fail("Structured rows never reach the parser"))
        ingestion.ingest(Item("drive", "a", "entities", "entities.csv",
            b"kind,name,id_namespace,external_id,email\n"
            b"person,Kevin Gu,demo:contact,kevin-gu,kevin@example.com\n"
            b"company,Trentcombe,corvus:common_id,13218,\n"
            b"fund,Lammwick,corvus:legal_entity,2254,\n"))
        before = len(self.graph.state.entities)
        header = ("subject_kind,subject_name,subject_ns,subject_id,predicate,"
                  "object_kind,object_name,object_ns,object_id,valid_from,valid_to\n")
        rows = ("person,Kevin Gu,demo:contact,kevin-gu,works_for,company,Trentcombe Fund Investors,corvus:common_id,13218,2026-07-01,\n"
                "company,Trentcombe,corvus:common_id,13218,invests_in,fund,Lammwick,corvus:legal_entity,2254,,\n"
                "company,Marlbank,demo:company,marlbank,administers,fund,Lammwick,corvus:legal_entity,2254,,\n")
        ingestion.ingest(Item("drive", "a", "relationships", "relationships.csv", (header + rows).encode()))
        self.assertEqual(len(self.graph.state.entities), before + 1)  # only Marlbank is new; other ends resolved by ID
        self.assertEqual(sorted(e.predicate for e in self.graph.state.edges.values()), ["administers", "invests_in", "works_for"])
        trentcombe = next(e for e in self.graph.state.entities.values() if e.kind == "company" and e.name == "Trentcombe")
        self.assertEqual(trentcombe.aliases, ["Trentcombe Fund Investors"])
        kevin = next(e for e in self.graph.state.entities.values() if e.kind == "person")
        self.assertEqual(self.graph.flatten(kevin.key, as_of=date(2026, 6, 30))["relationships"], [])
        self.assertEqual(len(self.graph.flatten(kevin.key, as_of=date(2026, 8, 1))["relationships"]), 1)
        before_bad_rows = self.graph.state.model_copy(deep=True)
        for bad in ("fund,Lammwick,corvus:legal_entity,2254,works_for,company,Trentcombe,corvus:common_id,13218,,\n",
                    "company,Trentcombe,corvus:common_id,13218,owns,fund,Lammwick,corvus:legal_entity,2254,,\n",
                    "company,Trentcombe,,,invests_in,fund,Lammwick,corvus:legal_entity,2254,,\n"):
            with self.assertRaises(ValueError):
                ingestion.ingest(Item("drive", "a", "bad" + bad[:12], "bad.csv", (header + bad).encode()))
        # A row rejected by validation neither creates nor touches either of its ends.
        self.assertEqual(self.graph.state.entities, before_bad_rows.entities)
        self.assertEqual(self.graph.state.edges, before_bad_rows.edges)


class ConnectorTests(unittest.TestCase):
    @patch.dict(os.environ, {"GOOGLE_ACCESS_TOKEN": "test", "GOOGLE_REFRESH_TOKEN": ""})
    def test_gmail_pagination_and_raw_decode(self):
        def handler(request):
            self.assertEqual(request.headers["authorization"], "Bearer test")
            if request.url.path.endswith("profile"):
                return httpx.Response(200, json={"emailAddress": "me@example.com"})
            if request.url.path.endswith("messages"):
                self.assertEqual(request.url.params["pageToken"], "page2")
                return httpx.Response(200, json={"messages": [{"id": "m"}], "nextPageToken": "page3"})
            return httpx.Response(200, json={"id": "m", "raw": base64.urlsafe_b64encode(b"From: a@example.com\n\nBody").decode().rstrip("=")})
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            items, token = GoogleConnector(client).page("gmail", "label:funds", "page2")
        self.assertEqual(token, "page3")
        self.assertIn(b"Body", items[0].content)

    @patch.dict(os.environ, {"GOOGLE_ACCESS_TOKEN": "test", "GOOGLE_REFRESH_TOKEN": ""})
    def test_drive_export_and_binary_and_pagination(self):
        exports = []
        def handler(request):
            if request.url.path.endswith("about"):
                return httpx.Response(200, json={"user": {"permissionId": "account"}})
            if request.url.path.endswith("files"):
                return httpx.Response(200, json={"nextPageToken": "more", "files": [
                    {"id": "s", "name": "Terms", "mimeType": "application/vnd.google-apps.spreadsheet", "version": "2"},
                    {"id": "p", "name": "letter.pdf", "mimeType": "application/pdf"}]})
            exports.append(str(request.url))
            return httpx.Response(200, content=b"content")
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            items, token = GoogleConnector(client).page("drive", "'folder' in parents")
        self.assertEqual(token, "more")
        self.assertEqual(items[0].filename, "Terms.xlsx")
        self.assertIn("export?mimeType=", exports[0])
        self.assertIn("alt=media", exports[1])

    @patch.dict(os.environ, {"GEMINI_API_KEY": "test"})
    def test_model_quote_validation(self):
        payload = {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps({
            "entities": [{"kind": "company", "name": "Invented", "quote": "Invented"}]})}]}}]}
        with patch("app.extraction.httpx.Client") as factory:
            response = factory.return_value.__enter__.return_value.post.return_value
            response.json.return_value = payload
            with self.assertRaisesRegex(ValueError, "quote absent"):
                gemini_extract("Actual source")


class ApiTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from app.main import app
        self.graph = Graph()
        self.source = fixture_source(self.graph)
        self.client = TestClient(app)
        self.store_patch = patch("app.graph_api.GraphStore")
        self.store = self.store_patch.start().return_value
        self.addCleanup(self.store_patch.stop)
        self.store.load_graph.side_effect = lambda: Graph(self.graph.state.model_copy(deep=True))
        def commit(graph):
            self.graph = graph
        self.store.save_graph.side_effect = commit

    def test_schema_and_typed_entity_api(self):
        self.assertEqual(self.client.get("/graph/schema").status_code, 200)
        body = {"entity": {"kind": "company", "name": "Manager", "external_ids": {"registry": "12"}}, "source_id": self.source}
        response = self.client.post("/graph/entities", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        entity_id = response.json()["entity"]["key"]
        self.assertEqual(self.client.get("/graph/entities/" + entity_id).status_code, 200)
        self.assertEqual(len(self.client.get("/graph/entities?kind=company").json()["entities"]), 1)
        body["entity"]["unrecognized_field"] = True
        self.assertEqual(self.client.post("/graph/entities", json=body).status_code, 422)

    def test_failed_connector_page_does_not_commit_or_advance(self):
        before = self.graph.state.model_dump(mode="json")
        with patch("app.graph_api.GoogleConnector") as connector:
            connector.return_value.page.return_value = ([
                Item("drive", "a", "one", "one.txt", b"valid"),
                Item("drive", "a", "two", "two.csv", b"kind,name,id_namespace,external_id\ninvalid,Bad,test,1\n")], "next")
            response = self.client.post("/connectors/sync", json={"provider": "drive", "query": "'folder' in parents"})
        self.assertEqual(response.status_code, 422, response.text)
        self.store.save_graph.assert_not_called()
        self.assertEqual(before, self.graph.state.model_dump(mode="json"))
        self.assertNotIn("next_page_token", response.json())

    def test_sync_creates_scoped_project_and_returns_cursor_after_commit(self):
        fund = self.graph.upsert("fund", "Fund", self.source)
        company = self.graph.upsert("company", "Manager", self.source)
        with patch("app.graph_api.GoogleConnector") as connector:
            connector.return_value.page.return_value = ([Item("gmail", "a", "email", "email.eml", b"From: dana@example.com\n\nQ3 work")], "next")
            response = self.client.post("/connectors/sync", json={"provider": "gmail", "query": "label:funds", "project_scope": {
                "fund_id": fund, "management_company_id": company, "quarter": "2026-Q3", "workflow_type": "fee_run"}})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["next_page_token"], "next")
        self.store.save_graph.assert_called_once()
        projects = [e for e in self.graph.state.entities.values() if e.kind == "project"]
        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0].status, "in_progress")


@unittest.skipUnless(os.environ.get("KG_DB_TESTS") == "1", "Set KG_DB_TESTS=1 for isolated SurrealDB integration")
class PersistenceTests(unittest.TestCase):
    def test_atomic_graph_projection_replay_and_revision_conflict(self):
        import uuid
        prefix = "testkg_" + uuid.uuid4().hex + "_"
        class IsolatedStore(GraphStore):
            def query(self, sql, variables=None):
                return super().query(sql.replace("kg_", prefix), variables)
        store = IsolatedStore()
        try:
            graph = store.load_graph()
            stale = store.load_graph()
            source = fixture_source(graph)
            person = graph.upsert("person", "Dana", source, emails=["dana@example.com"])
            graph.edge(person, "sent", source, source)
            store.save_graph(graph)
            store.save_graph(graph)
            restored = store.load_graph()
            self.assertEqual(restored.state.revision, 2)
            rows = store.query("SELECT ->kg_link->kg_node AS targets FROM kg_node WHERE kind = 'person';")[0]["result"]
            self.assertEqual(len(rows[0]["targets"]), 1)
            with self.assertRaises(RuntimeError):
                store.save_graph(stale)
            self.assertEqual(store.load_graph().state.revision, 2)
            # Old snapshots load without retaining their retired fact collection.
            store.query("UPDATE type::thing('kg_state', 'workspace') SET facts = $legacy;",
                        {"legacy": {"old": {"predicate": "currency", "value": "USD"}}})
            migrated = store.load_graph()
            self.assertNotIn("facts", migrated.state.model_dump(mode="json"))
            store.save_graph(migrated)
            saved = store.query("SELECT * FROM type::thing('kg_state', 'workspace');")[0]["result"][0]
            self.assertNotIn("facts", saved)
            self.assertIn(person, saved["entities"])
        finally:
            for table in ("kg_link", "kg_node", "kg_state"):
                store.query("REMOVE TABLE IF EXISTS " + table + ";")


if __name__ == "__main__":
    unittest.main()
