import base64
from datetime import datetime, timezone
import json
import os
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from svix.webhooks import Webhook

from mail_agent.main import app
from mail_agent.storage import key, tenant


class WebhookTests(unittest.TestCase):
    def setUp(self):
        self.secret = "whsec_" + base64.b64encode(b"a" * 32).decode()
        self.env = patch.dict(os.environ, {"AGENTMAIL_WEBHOOK_SECRET": self.secret, "AGENTMAIL_INBOX_ID": "agent@agentmail.to"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.client = TestClient(app)
        self.repo = Mock()
        self.repo.get.return_value = {"email": "person@example.com", "tenant": tenant("person@example.com")}
        self.event = {"event_type": "message.received", "message": {
            "inbox_id": "agent@agentmail.to", "message_id": "message", "thread_id": "thread",
            "from": "Person <person@example.com>", "extracted_text": "Run QC", "text": "Run QC\nQuoted earlier instructions",
        }}

    def post(self, event=None, age=0):
        body = json.dumps(event or self.event)
        now = datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() - age, timezone.utc)
        return self.client.post("/webhook", content=body, headers={"svix-id": "event", "svix-timestamp": str(int(now.timestamp())),
            "svix-signature": Webhook(self.secret).sign("event", now, body)})

    def test_valid_signed_message_resolves_verified_account_and_strips_quotes(self):
        with patch("mail_agent.main.repository", return_value=self.repo), patch("mail_agent.main.enqueue") as enqueue:
            self.assertEqual(self.post().status_code, 202)
        self.repo.get.assert_called_with("accounts", key("person@example.com"))
        value = self.repo.create.call_args.args[2]
        self.assertEqual(value["text"], "\n\nRun QC")
        self.assertEqual(value["tenant"], tenant("person@example.com"))
        enqueue.assert_called_once()

    def test_unsigned_or_expired_requests_are_rejected(self):
        with patch("mail_agent.main.repository") as repo:
            self.assertEqual(self.client.post("/webhook", json=self.event).status_code, 401)
            self.assertEqual(self.post(age=1000).status_code, 401)
        repo.assert_not_called()

    def test_unknown_sender_is_ignored(self):
        self.repo.get.return_value = None
        with patch("mail_agent.main.repository", return_value=self.repo), patch("mail_agent.main.enqueue") as queue:
            self.assertEqual(self.post().status_code, 202)
        queue.assert_not_called()

    def test_email_without_subject_is_accepted(self):
        self.event["message"]["subject"] = None
        with patch("mail_agent.main.repository", return_value=self.repo), patch("mail_agent.main.enqueue"):
            self.assertEqual(self.post().status_code, 202)

    def test_wrong_inbox_spam_unauthenticated_and_auto_replies_are_ignored(self):
        for change in [{"inbox_id": "other"}, {"labels": ["spam"]}, {"labels": ["unauthenticated"]},
                       {"headers": {"Auto-Submitted": "auto-replied"}}]:
            with self.subTest(change=change), patch("mail_agent.main.enqueue") as queue:
                self.assertEqual(self.post({**self.event, "message": {**self.event["message"], **change}}).status_code, 202)
                queue.assert_not_called()

    def test_queue_failure_is_not_acknowledged(self):
        client = TestClient(app, raise_server_exceptions=False)
        with patch("mail_agent.main.repository", return_value=self.repo), patch("mail_agent.main.enqueue", side_effect=RuntimeError()):
            self.client = client
            self.assertEqual(self.post().status_code, 500)

    def test_signup_queues_welcome_and_account_ingestion(self):
        account = {"email": "person@example.com", "tenant": tenant("person@example.com")}
        self.repo.reserve_ingestion.return_value = "ingestion-job"
        with patch("mail_agent.main.repository", return_value=self.repo), patch("mail_agent.main.enqueue") as queue:
            response = self.client.post("/signup", json={"email": account["email"]})
        self.assertEqual(response.status_code, 202)
        self.repo.create.assert_any_call("accounts", key(account["email"]), account)
        self.repo.reserve_ingestion.assert_called_once()
        self.assertEqual(queue.call_count, 2)
        self.assertEqual(queue.call_args_list[-1].args, ("ingestion-job",))


if __name__ == "__main__":
    unittest.main()
