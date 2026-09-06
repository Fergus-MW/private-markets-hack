import json
import os
import unittest
from unittest.mock import patch

from app.project_api import amount_at_stake, gate_run, normalized_check
from app.workflow_agents import dashboard_link, notification

TERMS_CHECKS = [
    {"check": "TC03", "tier": "a", "status": "FAIL", "name": "Fee drawn inside or outside commitment",
     "investors": "Trentcombe Fund Investors LLC", "n": 1, "amount": 22149.55, "detail": "unfunded_overstated_by=22149.55"},
    {"check": "TC01", "tier": "b", "status": "FAIL", "name": "Rate applied equals the register",
     "investors": "Trentcombe Fund Investors LLC", "n": 1, "amount": 900.0, "detail": "rate_applied=0.02"},
    {"check": "TC08", "tier": "b", "status": "PASS", "name": "Totals row foots", "investors": "", "n": 0,
     "amount": 0.0, "detail": "{}"},
    {"check": "TC00", "tier": "a", "status": "SKIPPED", "name": "Register row in force [needs the register]",
     "investors": "", "n": 0, "amount": 0.0, "detail": ""},
]
LOADER_CHECKS = [{"id": "T01", "tier": "a", "group": "tie-out", "severity": "hard", "status": "FAIL",
                  "observed": 18929, "expected": 18000, "check": "Row count ties to source"}]


class StubStore:
    """Just the two reads gate_run makes: the artifact metadata and its bytes."""

    def __init__(self, results):
        self.blob = json.dumps(results).encode()

    def get_record(self, table, record_id):
        return {"filename": record_id + ".xlsx", "sha256": "f" * 64}

    def read_artifact(self, artifact_id):
        return {"filename": "checks.json", "sha256": "d" * 64}, self.blob


def run_record(results, **overrides):
    return {"key": "r" * 64, "kind": "run", "gate": "terms", "mode": "terms", "as_of": "2026-06-30",
            "status": "completed", "turn": 1, "started_at": "2026-09-06T09:00:00+00:00",
            "inputs": {"draft": "draft-id", "terms": "terms-id"},
            "runtime": {"python": "3.12.6", "pandas": "2.2.2"},
            "output": {"artifacts": {"check_results": "checks-id"}, "release_ready": False},
            **overrides}


class DashboardTests(unittest.TestCase):
    def test_both_checkers_normalize_to_one_shape(self):
        terms = normalized_check(TERMS_CHECKS[0])
        self.assertEqual(terms["id"], "TC03")
        self.assertEqual(terms["name"], "Fee drawn inside or outside commitment")
        self.assertEqual(terms["who"], "Trentcombe Fund Investors LLC")
        self.assertEqual(terms["amount"], 22149.55)
        loader = normalized_check(LOADER_CHECKS[0])
        self.assertEqual(loader["id"], "T01")
        self.assertEqual(loader["name"], "Row count ties to source")
        self.assertEqual(loader["who"], "tie-out")
        self.assertEqual(loader["detail"], "observed 18929; expected 18000")

    def test_amount_at_stake_counts_tier_a_failures_only(self):
        # Tier b amounts are components of the same money; skipped and passing rows are not money at all.
        self.assertEqual(amount_at_stake([normalized_check(c) for c in TERMS_CHECKS]), 22149.55)

    def test_gate_run_prefers_the_checkers_own_amount_at_stake(self):
        store = StubStore({"checks": TERMS_CHECKS, "amount_at_stake": 22149.55, "entity": "Kestrel Lammwick Co-Invest LP",
                           "terms_rows_in_force": 19, "summary": {"FAIL": 2, "PASS": 1, "SKIPPED": 1}})
        run = gate_run(store, run_record({}))
        self.assertEqual(run["amount_at_stake"], 22149.55)
        self.assertEqual(run["entity"], "Kestrel Lammwick Co-Invest LP")
        self.assertEqual(run["terms_rows_in_force"], 19)
        self.assertEqual(run["inputs"]["draft"]["sha256"], "f" * 64)
        self.assertEqual(len(run["checks"]), 4)

    def test_gate_run_computes_the_amount_for_runs_recorded_before_the_checker_emitted_it(self):
        run = gate_run(StubStore({"checks": TERMS_CHECKS}), run_record({}))
        self.assertEqual(run["amount_at_stake"], 22149.55)

    def test_gate_run_survives_a_blocked_run_with_no_check_artifact(self):
        run = gate_run(StubStore({}), run_record({}, status="blocked",
                                                 output={"reason": "A named reviewer must ratify terms", "release_ready": False}))
        self.assertEqual(run["checks"], [])
        self.assertEqual(run["amount_at_stake"], 0)
        self.assertIn("ratify", run["reason"])


class NotificationTests(unittest.TestCase):
    def scoreboard(self, results):
        return notification(gate_run(StubStore(results), run_record({})))

    def test_five_lines_carry_the_checkers_numbers_and_the_worst_finding(self):
        lines = self.scoreboard({"checks": TERMS_CHECKS, "amount_at_stake": 22149.55,
                                 "entity": "Kestrel Lammwick Co-Invest LP"}).splitlines()
        self.assertEqual(len(lines), 5)
        self.assertIn("Kestrel Lammwick Co-Invest LP", lines[0])
        self.assertIn("as-of 2026-06-30", lines[0])
        self.assertEqual(lines[1], "Tier a 1 · Tier b 1 · Tier c 0 · decisions owed 0 · passes 1 of 4 · skipped 1 (no register)")
        self.assertEqual(lines[2], "Amount at stake (tier a): USD 22,149.55")
        # Tier a outranks the larger-amount rule; TC01 is tier b even though both failed.
        self.assertTrue(lines[3].startswith("Top finding: TC03 (tier a)"))
        self.assertIn("terms-id.xlsx", lines[4])

    def test_a_clean_run_says_so_instead_of_naming_an_amount(self):
        passing = [dict(check, status="PASS", amount=0.0) for check in TERMS_CHECKS]
        lines = self.scoreboard({"checks": passing}).splitlines()
        self.assertEqual(len(lines), 4)
        self.assertEqual(lines[2], "Nothing found. Every applicable check passed.")

    def test_a_run_with_no_checks_produces_no_notification(self):
        self.assertEqual(notification(gate_run(StubStore({}), run_record({}, output={"reason": "blocked"}))), "")


class DashboardLinkTests(unittest.TestCase):
    def test_link_deep_links_the_checker_run_and_stays_empty_without_an_origin(self):
        with patch.dict(os.environ, {"FRONTEND_PUBLIC_ORIGIN": "https://app.example/"}):
            self.assertEqual(dashboard_link("a" * 64), "https://app.example/dashboard/" + "a" * 64)
            self.assertEqual(dashboard_link("a" * 64, "b" * 64),
                             f"https://app.example/dashboard/{'a' * 64}?run={'b' * 64}")
        with patch.dict(os.environ, {"FRONTEND_PUBLIC_ORIGIN": ""}):
            self.assertEqual(dashboard_link("a" * 64, "b" * 64), "")


if __name__ == "__main__":
    unittest.main()
