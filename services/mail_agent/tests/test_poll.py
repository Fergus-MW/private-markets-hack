import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from mail_agent import main
from mail_agent.storage import key, tenant
from test_engine import Memory

FIRST, SECOND = "one@example.com", "two@example.com"


class PollTests(unittest.TestCase):
    def setUp(self):
        self.repo = Memory()
        main.repository.cache_clear()
        patcher = patch.object(main, "repository", lambda: self.repo)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.queued = []
        patcher = patch.object(main, "enqueue", self.queued.append)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(main.app)
        for email in (FIRST, SECOND):
            self.repo.create("accounts", key(email), {"email": email, "tenant": tenant(email)})
        # Memory.list() serves this attribute, matching how test_engine drives it.
        self.repo.accounts = [{"email": email, "tenant": tenant(email)} for email in (FIRST, SECOND)]

    def finish(self, email, retry_safe=True, status="completed"):
        """Mark an account's current run finished, as advance() would."""
        identifier = self.repo.get("accounts", key(email))["ingestion_job"]
        job = self.repo.get("jobs", identifier)
        job.update(done=True, result={"status": status, "retry_safe": retry_safe, "summary": "done"})

    def test_every_account_starts_a_run_and_is_queued(self):
        body = self.client.post("/ingestion/poll").json()
        self.assertEqual((body["started"], body["held"], body["skipped"]), (2, 0, 0))
        self.assertEqual(len(self.queued), 2)
        for email in (FIRST, SECOND):
            self.assertIsNotNone(self.repo.get("accounts", key(email)).get("ingestion_job"))

    def test_a_run_still_in_flight_is_never_restarted(self):
        self.client.post("/ingestion/poll")
        with patch("mail_agent.main.time.time", return_value=10 ** 9):
            body = self.client.post("/ingestion/poll").json()
        self.assertEqual((body["started"], body["held"]), (0, 2), "in-flight runs must be held")

    def test_a_new_window_starts_a_run_once_the_previous_one_finished(self):
        self.client.post("/ingestion/poll")
        for email in (FIRST, SECOND):
            self.finish(email)
        with patch("mail_agent.main.time.time", return_value=10 ** 9):
            body = self.client.post("/ingestion/poll").json()
        self.assertEqual((body["started"], body["held"]), (2, 0), "new mail should get a fresh run")

    def test_the_same_window_does_not_start_a_second_run(self):
        """Scheduler retries inside one window must not double-run an account."""
        self.client.post("/ingestion/poll")
        for email in (FIRST, SECOND):
            self.finish(email)
        body = self.client.post("/ingestion/poll").json()
        self.assertEqual((body["started"], body["held"]), (0, 2))

    def test_an_unconfirmed_run_blocks_polling_rather_than_duplicating_work(self):
        """advance() returns retry_safe False when it could not confirm a launch."""
        self.client.post("/ingestion/poll")
        self.finish(FIRST, retry_safe=False, status="unknown")
        self.finish(SECOND)
        with patch("mail_agent.main.time.time", return_value=10 ** 9):
            body = self.client.post("/ingestion/poll").json()
        self.assertEqual((body["started"], body["held"]), (1, 1))

    def test_window_is_configurable_and_floored(self):
        with patch.dict("os.environ", {"INGESTION_POLL_WINDOW_SECONDS": "1"}):
            self.assertEqual(self.client.post("/ingestion/poll").json()["window"], 60)
        with patch.dict("os.environ", {"INGESTION_POLL_WINDOW_SECONDS": "900"}):
            self.assertEqual(self.client.post("/ingestion/poll").json()["window"], 900)


if __name__ == "__main__":
    unittest.main()
