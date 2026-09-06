import unittest
from unittest.mock import patch

from app.drive import DeliveryError, safe_name
from app.workflow_agents import deliver_draft

PROJECT = {"name": "Fund A", "quarter": "2026Q2", "key": "p1"}


class SafeNameTests(unittest.TestCase):
    def test_strips_path_and_control_characters(self):
        self.assertEqual(safe_name("a/b\\c\x00d"), "a b c d")

    def test_never_returns_an_empty_drive_name(self):
        self.assertEqual(safe_name("   "), "first-run draft")


class DeliverDraftTests(unittest.TestCase):
    def test_delivered_workbook_is_reported_with_its_link(self):
        sent = {"id": "file1", "name": "Fund A 2026Q2 first-run draft.xlsx", "webViewLink": "https://drive/x"}
        with patch("app.workflow_agents.deliver", return_value=sent) as upload:
            note, records = deliver_draft("run", PROJECT, b"xlsx")
        self.assertIn("https://drive/x", note)
        self.assertEqual(records[0]["status"], "delivered")
        self.assertEqual(records[0]["drive_file_id"], "file1")
        # The run is the audit key; the upload must carry it back to the project.
        self.assertEqual(upload.call_args.args[4]["run_id"], "run")

    def test_delivery_failure_never_raises_and_explains_itself(self):
        with patch("app.workflow_agents.deliver", side_effect=DeliveryError("stored permission is read-only")):
            note, records = deliver_draft("run", PROJECT, b"xlsx")
        self.assertIn("read-only", note)
        self.assertEqual(records[0]["status"], "failed")

    def test_unexpected_failure_is_contained_without_leaking_detail(self):
        with patch("app.workflow_agents.deliver", side_effect=RuntimeError("token abc123 rejected for user@x")):
            note, records = deliver_draft("run", PROJECT, b"xlsx")
        self.assertNotIn("abc123", note)
        self.assertEqual(records[0]["status"], "failed")

    def test_rules_only_draft_sends_nothing(self):
        with patch("app.workflow_agents.deliver") as upload:
            self.assertEqual(deliver_draft("run", PROJECT, None), ("", []))
        upload.assert_not_called()


if __name__ == "__main__":
    unittest.main()
