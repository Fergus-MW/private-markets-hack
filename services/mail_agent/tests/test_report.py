import unittest
from unittest.mock import Mock

from mail_agent.ingestion import Ingestion
from mail_agent.storage import key, tenant
from test_engine import Memory

EMAIL = "person@example.com"


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.repo, self.client = Memory(), Mock()
        self.manager = Ingestion(self.repo, Mock(), self.client)
        self.account = {"email": EMAIL, "tenant": tenant(EMAIL)}
        self.identifier = key("run")

    def account_with_job(self, job):
        self.repo.create("accounts", key(EMAIL), {**self.account, "ingestion_job": self.identifier})
        self.repo.create("jobs", self.identifier, job)

    def test_no_run_is_not_done_and_matches_the_agent_wording(self):
        self.repo.create("accounts", key(EMAIL), self.account)
        report = self.manager.report(self.account)
        self.assertEqual(report["state"], "none")
        self.assertFalse(report["done"])
        self.assertEqual(report["summary"], self.manager.status(self.account))

    def test_running_run_reports_live_per_provider_counts(self):
        self.account_with_job({"kind": "ingestion", **self.account})
        self.client.progress.side_effect = lambda account, run, provider: (
            {"status": "running", "counts": {"ingested": 3}} if provider == "drive" else None)
        report = self.manager.report(self.account)
        self.assertFalse(report["done"])
        self.assertEqual(report["state"], "running")
        drive, gmail = report["providers"]
        self.assertEqual((drive["provider"], drive["status"], drive["checked"]), ("drive", "running", 3))
        self.assertEqual((gmail["provider"], gmail["status"], gmail["checked"]), ("gmail", "queued", 0))
        self.assertEqual(report["summary"], self.manager.status(self.account))

    def test_completed_run_is_done_and_carries_final_counts(self):
        self.account_with_job({"kind": "ingestion", **self.account,
                               "providers": {"drive": {"status": "completed", "counts": {"ingested": 2}},
                                             "gmail": {"status": "completed", "counts": {"ingested": 1}}},
                               "result": {"status": "completed", "counts": {"ingested": 3}, "summary": "Ready."}})
        report = self.manager.report(self.account)
        self.assertTrue(report["done"])
        self.assertEqual(report["state"], "completed")
        self.assertEqual(report["counts"], {"ingested": 3})
        self.assertEqual([p["checked"] for p in report["providers"]], [2, 1])

    def test_unconfirmed_run_is_done_but_never_reported_as_completed(self):
        """advance() refuses to guess readiness; report() must not launder that."""
        self.account_with_job({"kind": "ingestion", **self.account,
                               "result": {"status": "unknown", "summary": "Could not confirm."}})
        report = self.manager.report(self.account)
        self.assertTrue(report["done"])
        self.assertNotEqual(report["state"], "completed")
        self.assertEqual(report["state"], "unknown")


if __name__ == "__main__":
    unittest.main()
