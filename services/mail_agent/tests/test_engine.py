import copy
import unittest
from unittest.mock import Mock

from mail_agent.engine import Engine
from mail_agent.storage import Busy, key


class Memory:
    def __init__(self):
        self.rows = {}

    def create(self, collection, identifier, value):
        self.rows.setdefault(identifier, copy.deepcopy(value))

    def list(self, collection, limit=50):
        return copy.deepcopy(getattr(self, "accounts", []))

    def get(self, collection, identifier):
        return self.rows.get(identifier)

    def remember(self, thread_id, job_id, event):
        self.rows[thread_id] = {"events": [{"job_id": job_id, **event}]}

    def claim(self, identifier):
        row = self.rows[identifier]
        if row.get("done"):
            return None
        if row.get("lease_until", 0):
            raise Busy()
        row.update(lease="lease", lease_until=1)
        return copy.deepcopy(row)

    def save(self, identifier, token, **updates):
        assert token == self.rows[identifier]["lease"]
        self.rows[identifier].update(copy.deepcopy(updates))


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.repo, self.queue, self.mailer, self.graph, self.router = Memory(), Mock(), Mock(), Mock(), Mock()
        self.engine = Engine(self.repo, self.queue, self.mailer, lambda account: self.graph, self.router)
        self.call = {"name": "trigger_qc_gate", "args": {"project_id": "a" * 64, "instructions": "Run QC"}}
        self.account = {"email": "person@example.com", "tenant": "u-person", "message_id": "message"}

    def incoming(self):
        self.repo.create("jobs", "incoming", {"kind": "incoming", "text": "Run QC", **self.account})
        self.router.return_value = {"tool_call": self.call}

    def test_reconnect_only_sends_one_welcome(self):
        for _ in range(2):
            self.repo.create("jobs", "welcome", {"kind": "welcome", **self.account})
            self.engine.process("welcome")
        self.mailer.send.assert_called_once()

    def test_start_before_dispatch_and_separate_worker_completion(self):
        self.incoming()
        events = []
        self.mailer.send.side_effect = lambda *args: events.append("email")
        self.queue.side_effect = lambda *args: events.append("enqueue")
        self.engine.process("incoming")
        self.assertEqual(events, ["email", "enqueue"])
        self.graph.call.assert_not_called()
        self.graph.call.return_value = {"status": "blocked", "summary": "Missing terms ratification"}
        worker = key("incoming", "workflow")
        self.engine.process(worker)
        self.assertIn("Missing terms ratification", self.mailer.send.call_args.args[1])
        self.assertEqual(self.graph.call.call_count, 1)
        self.engine.process(worker)
        self.assertEqual(self.graph.call.call_count, 1)

    def test_completion_email_retry_never_reexecutes_workflow(self):
        self.repo.create("jobs", "worker", {"kind": "workflow", "tool_call": self.call, **self.account})
        self.graph.call.return_value = {"status": "completed", "summary": "Done"}
        self.mailer.send.side_effect = RuntimeError("mail unavailable")
        with self.assertRaises(RuntimeError):
            self.engine.process("worker")
        self.mailer.send.side_effect = None
        self.engine.process("worker")
        self.graph.call.assert_called_once()

    def test_queue_failure_retries_without_second_start_email(self):
        self.incoming()
        self.queue.side_effect = RuntimeError("queue unavailable")
        with self.assertRaises(RuntimeError):
            self.engine.process("incoming")
        self.queue.side_effect = None
        self.engine.process("incoming")
        self.mailer.send.assert_called_once()
        self.router.assert_called_once()

    def test_no_workflow_without_function_call(self):
        self.incoming()
        self.router.return_value = {"reply": "Which quarter?"}
        self.engine.process("incoming")
        self.queue.assert_not_called()
        self.graph.call.assert_not_called()

    def test_worker_failure_sends_terminal_email(self):
        self.repo.create("jobs", "worker", {"kind": "workflow", "tool_call": self.call, **self.account})
        self.graph.call.side_effect = RuntimeError("down")
        for _ in range(2):
            with self.assertRaises(RuntimeError):
                self.engine.process("worker")
        self.engine.process("worker")
        self.assertIn("failed", self.mailer.send.call_args.args[1])
        self.assertTrue(self.repo.rows["worker"]["done"])

    def test_first_run_dispatch(self):
        self.call["name"] = "trigger_first_run"
        self.repo.create("jobs", "worker", {"kind": "workflow", "tool_call": self.call, **self.account})
        self.graph.call.return_value = {"status": "completed", "summary": "Draft ready for review"}
        self.engine.process("worker")
        self.assertTrue(self.graph.call.call_args.args[1].endswith("/agents/first-run"))

    def test_explain_dispatch_reads_recorded_results(self):
        self.call["name"] = "explain_run"
        self.repo.create("jobs", "worker", {"kind": "workflow", "tool_call": self.call, **self.account})
        self.graph.call.return_value = {"status": "completed", "summary": "The QC gate flagged two checks"}
        self.engine.process("worker")
        self.assertTrue(self.graph.call.call_args.args[1].endswith("/agents/explain"))
        self.assertIn("flagged two checks", self.mailer.send.call_args.args[1])

    def test_project_question_dispatches_answer_agent(self):
        self.call = {"name": "answer_project_question", "args": {
            "project_id": "a" * 64, "question": "Who manages this fund?"}}
        self.repo.create("jobs", "worker", {"kind": "workflow", "tool_call": self.call, **self.account})
        self.graph.call.return_value = {"status": "completed", "summary": "Manager A.\n\nSources: evidence.txt"}
        self.engine.process("worker")
        self.assertTrue(self.graph.call.call_args.args[1].endswith("/agents/answer"))
        self.assertEqual(self.graph.call.call_args.args[2]["instructions"], "Who manages this fund?")

    def test_project_and_workspace_graph_links_are_returned_without_queueing_work(self):
        for name, args, link in (
                ("get_project_graph_link", {"project_id": "a" * 64}, "https://example.test/graphs/" + "a" * 64),
                ("get_workspace_graph_link", {}, "https://example.test/graphs/workspace")):
            with self.subTest(name=name):
                self.repo.rows.clear()
                self.mailer.reset_mock()
                self.queue.reset_mock()
                self.graph.visualization.return_value = link
                self.call = {"name": name, "args": args}
                self.incoming()
                self.engine.process("incoming")
                self.assertEqual(self.mailer.send.call_count, 2)
                self.assertIn(link, self.mailer.send.call_args.args[1])
                self.queue.assert_not_called()

    def test_live_workflow_status_returns_verbose_trace_without_starting_work(self):
        task_id = "b" * 64
        self.repo.create("jobs", task_id, {"kind": "workflow", "tenant": self.account["tenant"],
            "tool_call": self.call, **self.account, "lease_until": 99})
        self.repo.create("jobs", "status", {"kind": "incoming", "text": "show detailed status",
            **self.account})
        self.router.return_value = {"tool_call": {"name": "check_workflow_status", "args": {
            "project_id": "a" * 64, "job_id": task_id, "verbose": True}}}
        self.graph.call.return_value = {"status": "running", "phase": "reviewing", "trace": [{
            "at": "2026-09-06T10:00:00Z", "phase": "reviewing", "status": "running",
            "message": "Independent reviewer is checking the draft.", "details": {"sheet_count": 2}}]}

        self.engine.process("status")

        text = self.mailer.send.call_args.args[1]
        self.assertIn("is running", text)
        self.assertIn("Current phase: reviewing", text)
        self.assertIn("Verbose execution trace", text)
        self.assertIn('"sheet_count": 2', text)
        self.queue.assert_not_called()
        self.assertTrue(self.graph.call.call_args.args[1].endswith("/agents/jobs/" + task_id))

    def test_workflow_status_cannot_read_another_tenants_task(self):
        task_id = "b" * 64
        self.repo.create("jobs", task_id, {"kind": "workflow", "tenant": "u-other",
            "tool_call": self.call, **{**self.account, "tenant": "u-other"}})
        self.repo.create("jobs", "status", {"kind": "incoming", "text": "status", **self.account})
        self.router.return_value = {"tool_call": {"name": "check_workflow_status", "args": {
            "project_id": "a" * 64, "job_id": task_id, "verbose": False}}}

        self.engine.process("status")

        self.assertIn("couldn't find", self.mailer.send.call_args.args[1])
        self.graph.call.assert_not_called()

    def test_started_task_id_is_retained_in_thread_context_for_status_followups(self):
        self.incoming()
        self.engine.process("incoming")
        thread = self.repo.rows[key(self.account["tenant"], "incoming")]
        self.assertEqual(thread["events"][0]["tool_result"]["job_id"], key("incoming", "workflow"))


class ClientMailTests(unittest.TestCase):
    def setUp(self):
        self.repo, self.queue, self.mailer, self.router = Memory(), Mock(), Mock(), Mock()
        self.graphs = {}
        self.engine = Engine(self.repo, self.queue, self.mailer, self.graph_for, self.router)
        self.mailer.raw.return_value = b"From: ada@client.com\r\n\r\nbody"
        self.repo.accounts = [{"email": "one@fund.com", "tenant": "u-1"},
                              {"email": "two@fund.com", "tenant": "u-2"}]
        self.job = {"kind": "client_mail", "sender": "ada@client.com", "message_id": "m1",
                    "thread_id": "t1", "subject": "Q2 statement", "recipients": ["two@fund.com"]}

    def graph_for(self, account):
        return self.graphs[account["tenant"]]

    def graph(self, known, source_id="s1", projects=()):
        client = Mock()
        client.call.return_value = {"known": known}
        client.upload.return_value = {"source_id": source_id, "attachments": 2, "projects": list(projects)}
        return client

    def run_job(self):
        self.repo.create("jobs", "cm", self.job)
        self.engine.process("cm")
        return self.repo.rows["cm"]["result"]

    def test_ingests_only_into_graphs_that_know_the_sender(self):
        self.graphs = {"u-1": self.graph(False), "u-2": self.graph(True, projects=[{"project_id": "p1"}])}
        result = self.run_job()
        self.assertEqual([row["tenant"] for row in result["ingested"]], ["u-2"])
        self.graphs["u-1"].upload.assert_not_called()
        self.graphs["u-2"].upload.assert_called_once()

    def test_an_unknown_sender_reaches_no_graph_and_sends_no_mail(self):
        self.graphs = {"u-1": self.graph(False), "u-2": self.graph(False)}
        result = self.run_job()
        self.assertEqual(result["ingested"], [])
        self.assertEqual(result["graphs_checked"], 2)
        for client in self.graphs.values():
            client.upload.assert_not_called()
        self.mailer.send.assert_not_called()

    def test_client_mail_is_never_routed_to_the_assistant(self):
        self.graphs = {"u-1": self.graph(True), "u-2": self.graph(True)}
        self.run_job()
        self.router.assert_not_called()

    def test_addressed_account_is_checked_first(self):
        order = []
        self.graphs = {tenant: self.graph(False) for tenant in ("u-1", "u-2")}
        for tenant, client in self.graphs.items():
            client.call.side_effect = lambda *a, _t=tenant: (order.append(_t), {"known": False})[1]
        self.run_job()
        self.assertEqual(order, ["u-2", "u-1"])

    def test_the_sender_address_is_escaped_into_the_lookup_path(self):
        self.job["sender"] = "a b/../evil@client.com"
        self.graphs = {"u-1": self.graph(False), "u-2": self.graph(False)}
        self.run_job()
        path = self.graphs["u-2"].call.call_args.args[1]
        self.assertEqual(path, "/mail/senders/a%20b%2F..%2Fevil%40client.com")


if __name__ == "__main__":
    unittest.main()
