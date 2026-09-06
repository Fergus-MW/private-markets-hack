import io
import unittest
from unittest.mock import patch
from fastapi import HTTPException
from openpyxl import load_workbook
from app.workflow_agents import (Draft, ProjectAnswer, agent_status, findings,
                                 validate_answer, validate_draft, draft_workbook,
                                 context_text)


class DraftTests(unittest.TestCase):
    def draft(self, quote="Fee is 1%"):
        return Draft.model_validate({"summary": "Draft", "missing": [], "rules": [], "sheets": [{
            "name": "Schedule", "headers": ["Investor", "Fee"], "rows": [["=HYPERLINK(\"evil\")", 10]],
            "evidence": [{"source_id": "source", "quote": quote}]}]})

    def test_rejects_invented_citations(self):
        with self.assertRaises(ValueError):
            validate_draft(self.draft("Fee is 9%"), {"sources": {"source": {"text": "Fee is 1%"}}})

    def test_rejects_ragged_or_duplicate_sheets(self):
        draft = self.draft()
        draft.sheets[0].rows = [[1]]
        with self.assertRaises(ValueError):
            validate_draft(draft, {"sources": {"source": {"text": "Fee is 1%"}}})

    def test_draft_cells_are_data_never_formulas(self):
        draft = self.draft()
        validate_draft(draft, {"sources": {"source": {"text": "Fee is 1%"}}})
        book = load_workbook(io.BytesIO(draft_workbook(draft)))
        self.assertEqual(book["Schedule"]["A2"].data_type, "s")
        self.assertEqual(book["Schedule"]["B2"].value, 10)
        book.close()


class FindingTests(unittest.TestCase):
    def items(self):
        return [{"topic": "rule", "detail": "Fee is 1%"}, {"topic": "missing", "detail": "Invested capital"}]

    def test_findings_are_queryable_records_keyed_per_run(self):
        rows = findings("run", "first-run", self.items())
        self.assertEqual([row["kind"] for row in rows], ["finding", "finding"])
        self.assertTrue(all(row["run_id"] == "run" and row["workflow"] == "first-run" for row in rows))
        self.assertEqual({row["topic"] for row in rows}, {"rule", "missing"})
        self.assertEqual(len({row["key"] for row in rows}), 2)

    def test_finding_keys_are_stable_so_replay_stays_idempotent(self):
        self.assertEqual([row["key"] for row in findings("run", "first-run", self.items())],
                         [row["key"] for row in findings("run", "first-run", self.items())])
        self.assertNotEqual([row["key"] for row in findings("run", "first-run", self.items())],
                            [row["key"] for row in findings("other", "first-run", self.items())])


class AnswerTests(unittest.TestCase):
    def test_supported_answers_require_exact_project_source_citations(self):
        context = {"sources": {"source": {"text": "Manager A manages Fund A."}}}
        answer = ProjectAnswer(answer="Manager A", supported=True,
            evidence=[{"source_id": "source", "quote": "Manager A manages Fund A."}], limitations=[])
        validate_answer(answer, context)
        answer.evidence[0].quote = "Manager B manages Fund A."
        with self.assertRaises(ValueError):
            validate_answer(answer, context)

    def test_unsupported_answer_may_report_missing_evidence_without_a_citation(self):
        answer = ProjectAnswer(answer="The evidence does not say.", supported=False, evidence=[],
                               limitations=["No management agreement is present"])
        validate_answer(answer, {"sources": {}})
        answer.supported = True
        with self.assertRaises(ValueError):
            validate_answer(answer, {"sources": {}})


class StatusTests(unittest.TestCase):
    @patch("app.workflow_agents.local_store")
    def test_status_reads_the_run_by_durable_mail_job_id(self, local):
        task_id = "a" * 64
        local.return_value.agent_run.return_value = {
            "status": "running", "phase": "reviewing",
            "trace": [{"phase": "reviewing", "message": "Review in progress"}],
        }
        self.assertEqual(agent_status("b" * 64, task_id)["phase"], "reviewing")
        local.return_value.agent_run.assert_called_once_with(task_id)

    @patch("app.workflow_agents.local_store")
    def test_status_rejects_invalid_or_unknown_task_ids(self, local):
        with self.assertRaises(HTTPException) as invalid:
            agent_status("b" * 64, "not-a-task")
        self.assertEqual(invalid.exception.status_code, 422)
        local.return_value.agent_run.return_value = None
        with self.assertRaises(HTTPException) as missing:
            agent_status("b" * 64, "a" * 64)
        self.assertEqual(missing.exception.status_code, 404)


class CachePrefixTests(unittest.TestCase):
    def test_later_agent_fields_do_not_move_the_project_evidence_prefix(self):
        base = {"project": {"key": "p"}, "instructions": "run", "sources": {"s": {"text": "evidence"}},
                "evidence_truncated": False}
        first = context_text(base)
        review = context_text({**base, "draft": {"summary": "draft"}})
        self.assertTrue(review.startswith(first[:-1] + ","))


if __name__ == "__main__":
    unittest.main()
