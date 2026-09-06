import copy
import os
import time
import unittest
from unittest.mock import Mock, patch

from mail_agent.engine import Engine
from mail_agent.ingestion import ConnectorClient, Ingestion
from mail_agent.storage import Busy, key, tenant
from test_engine import Memory


class IngestionTests(unittest.TestCase):
    def setUp(self):
        self.repo, self.queue, self.client = Memory(), Mock(), Mock()
        self.manager = Ingestion(self.repo, self.queue, self.client)
        self.identifier = key("run")
        self.job = {"email": "person@example.com", "tenant": tenant("person@example.com"), "kind": "ingestion"}
        self.mailer, self.graph, self.router = Mock(), Mock(), Mock()
        self.engine = Engine(self.repo, self.queue, self.mailer, lambda job: self.graph, self.router, self.manager)
        self.repo.create("jobs", self.identifier, self.job)
        self.client.executions.return_value = []
        self.client.start.return_value = "operation"

    def save(self, **updates):
        self.job.update(copy.deepcopy(updates))

    def completed(self, status="completed", counts=None):
        self.client.executions.return_value = [{"completionTime": "now", "taskCount": 1, "succeededCount": 1}]
        self.client.progress.return_value = {"status": status, "counts": counts if counts is not None else {"ingested": 2}}

    def test_launch_is_persisted_and_not_repeated_after_ambiguous_timeout(self):
        self.client.start.side_effect = TimeoutError()
        with self.assertRaises(TimeoutError):
            self.manager.advance(self.identifier, self.job, self.save)
        with self.assertRaises(Busy):
            self.manager.advance(self.identifier, self.job, self.save)
        self.client.start.assert_called_once()
        self.completed()
        self.assertEqual(self.manager.advance(self.identifier, self.job, self.save)["status"], "completed")
        self.client.start.assert_called_once()

    def test_start_and_finish_mail_are_exactly_once_across_monitor_retries(self):
        for _ in range(2):
            with self.assertRaises(Busy):
                self.engine.process(self.identifier)
        self.mailer.send.assert_called_once()
        self.completed()
        self.engine.process(self.identifier)
        self.engine.process(self.identifier)
        self.assertEqual(self.mailer.send.call_count, 2)
        self.assertIn("knowledge graph is built and ready", self.mailer.send.call_args.args[1])

    def test_graph_ready_requires_both_providers_and_cloud_execution_success(self):
        self.completed()
        self.client.executions.side_effect = lambda run, provider: ([{"taskCount": 1, "succeededCount": 1, "completionTime": "now"}] if provider == "drive" else [{"taskCount": 1}])
        with self.assertRaises(Busy):
            self.manager.advance(self.identifier, self.job, self.save)
        self.assertTrue(self.job["providers"]["drive"]["finished"])
        self.client.start.assert_not_called()

    def test_partial_empty_and_failed_scans_never_claim_ready(self):
        for status, counts, expected in [("partial", {"ingested": 2, "archive_only": 1}, "partial"),
                                         ("completed", {}, "empty"), ("empty", {}, "empty"),
                                         ("failed", {"failed": 1}, "failed")]:
            with self.subTest(status=status, counts=counts):
                self.job.pop("providers", None)
                self.completed(status, counts)
                result = self.manager.advance(self.identifier, self.job, self.save)
                self.assertEqual(result["status"], expected)
                self.assertNotIn("It's ready to go", result["summary"])
        self.job.pop("providers", None)
        self.completed()
        self.client.executions.return_value = [{"completionTime": "now", "failedCount": 1, "taskCount": 1}]
        self.assertEqual(self.manager.advance(self.identifier, self.job, self.save)["status"], "failed")

    def test_one_empty_provider_does_not_block_valid_ingested_sources(self):
        self.client.executions.return_value = [{"completionTime": "now", "taskCount": 1, "succeededCount": 1}]
        self.client.progress.side_effect = lambda job, run, provider: (
            {"status": "empty", "counts": {}} if provider == "drive"
            else {"status": "completed", "counts": {"ingested": 2}})
        result = self.manager.advance(self.identifier, self.job, self.save)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["counts"], {"ingested": 2})

    def test_uncertain_launch_blocks_automatic_retry(self):
        self.job["providers"] = {"drive": {"launching_at": time.time() - 1000}}
        result = self.manager.advance(self.identifier, self.job, self.save)
        self.assertFalse(result["retry_safe"])
        self.client.start.assert_not_called()

    def test_completion_email_failure_does_not_repeat_connectors(self):
        self.completed()
        self.mailer.send.side_effect = [None, RuntimeError("mail down"), None]
        with self.assertRaises(RuntimeError):
            self.engine.process(self.identifier)
        calls = self.client.executions.call_count
        self.engine.process(self.identifier)
        self.assertEqual(self.client.executions.call_count, calls)

    def test_status_recovery_tool_works_when_graph_is_down(self):
        self.graph.projects.side_effect = RuntimeError("graph unavailable")
        self.router.return_value = {"tool_call": {"name": "check_ingestion_status", "args": {}}}
        self.repo.create("jobs", "incoming", {**self.job, "kind": "incoming", "text": "Is ingestion finished?", "message_id": "message"})
        self.engine.process("incoming")
        self.assertEqual(self.mailer.send.call_count, 2)
        self.assertIn("No ingestion run", self.mailer.send.call_args.args[1])
        self.queue.assert_not_called()

    def test_execution_overrides_derive_credentials_from_verified_account(self):
        client = ConnectorClient.__new__(ConnectorClient)
        client.job, client.session = "projects/test/locations/europe-west2/jobs/connector", Mock()
        client.session.post.return_value.json.return_value = {"name": "operation"}
        with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "test"}):
            client.start(self.job, self.identifier, "drive")
            with self.assertRaises(ValueError):
                client.start({**self.job, "tenant": tenant("other@example.com")}, self.identifier, "drive")
        body = client.session.post.call_args.kwargs["json"]
        env = {v["name"]: v["value"] for v in body["overrides"]["containerOverrides"][0]["env"]}
        self.assertEqual(env["CONNECTOR_SECRET"], f"projects/test/secrets/connector-{self.job['tenant']}-oauth/versions/latest")
        self.assertEqual(env["GRAPH_USE_GEMINI"], "true")
        self.assertEqual(env["SOURCE_QUERY"], "")

    def test_progress_identity_is_checked(self):
        client = ConnectorClient.__new__(ConnectorClient)
        client.bucket, client.session = "bucket", Mock()
        client.session.get.return_value.status_code = 200
        client.session.get.return_value.json.return_value = {"tenant": "someone-else", "run_id": self.identifier, "provider": "drive"}
        with self.assertRaises(ValueError):
            client.progress(self.job, self.identifier, "drive")
