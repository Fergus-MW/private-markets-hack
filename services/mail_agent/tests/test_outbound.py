import unittest
from unittest.mock import Mock, patch

from mail_agent.clients import SIGN_OFF, polish, polish_subject
from mail_agent.engine import WELCOME


class PolishTests(unittest.TestCase):
    def test_every_message_signs_off_the_same_way(self):
        self.assertTrue(polish("Your task has finished.").endswith(SIGN_OFF))

    def test_the_sign_off_is_never_repeated(self):
        once = polish("Your task has finished.")
        self.assertEqual(polish(once).count("Best wishes"), 1)

    def test_em_dashes_from_model_output_never_reach_the_reader(self):
        for text in ["The check failed — the totals disagree.",
                     "Fund A—Q2 2026 is blocked.",
                     "One point ― another point."]:
            polished = polish(text)
            self.assertNotIn("—", polished)
            self.assertNotIn("―", polished)

    def test_replacing_a_dash_leaves_readable_punctuation(self):
        self.assertIn("The check failed, the totals disagree.", polish("The check failed — the totals disagree."))
        self.assertNotIn(", ,", polish("Blocked —, missing terms"))
        self.assertNotIn("  ", polish("Fund A  —  Q2"))

    def test_subjects_are_cleaned_without_a_sign_off(self):
        self.assertEqual(polish_subject("Your knowledge graph — ingestion update"),
                         "Your knowledge graph: ingestion update")
        self.assertNotIn("Best wishes", polish_subject("Ingestion update"))

    def test_body_text_survives_intact(self):
        body = "Your task has finished with status: completed.\n\nTask: abc"
        self.assertTrue(polish(body).startswith(body))


class WelcomeToneTests(unittest.TestCase):
    def test_the_welcome_carries_no_em_dashes(self):
        self.assertNotIn("—", WELCOME)

    def test_the_welcome_leaves_the_sign_off_to_the_mailer(self):
        self.assertNotIn("Best wishes", WELCOME)
        self.assertTrue(polish(WELCOME).endswith(SIGN_OFF))


class SendTests(unittest.TestCase):
    def send(self, job, text):
        from mail_agent.clients import Mailer
        mailer = Mailer.__new__(Mailer)
        mailer.client = Mock()
        with patch.dict("os.environ", {"AGENTMAIL_INBOX_ID": "agent@agentmail.to"}):
            mailer.send(job, text)
        return mailer.client

    def test_replies_are_polished_before_sending(self):
        client = self.send({"email": "a@b.com", "message_id": "m", "delivery_key": "k"},
                           "Blocked — terms are missing.")
        sent = client.inboxes.messages.reply.call_args.kwargs["text"]
        self.assertNotIn("—", sent)
        self.assertTrue(sent.endswith(SIGN_OFF))

    def test_new_messages_polish_body_and_subject(self):
        client = self.send({"email": "a@b.com", "delivery_key": "k",
                            "subject": "Your graph — update"}, "Ready — all done.")
        kwargs = client.inboxes.messages.send.call_args.kwargs
        self.assertEqual(kwargs["subject"], "Your graph: update")
        self.assertNotIn("—", kwargs["text"])
        self.assertTrue(kwargs["text"].endswith(SIGN_OFF))


if __name__ == "__main__":
    unittest.main()
