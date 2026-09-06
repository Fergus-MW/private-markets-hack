import json
import unittest
from datetime import date
from unittest.mock import patch

from app.connectors import Item
from app.extraction import Ingestion
from app.graph import Graph, Source, key
from app.term_proposals import (list_proposals, propose_for_source, ratify, sender_from_graph,
                                term_lines)
from app.terms import terms_as_of

TERMS_V2 = (
    "investor_id,investor_name,mgmt_fee_rate_pa,fee_basis,fee_offset_pct,cas_deadline_days,notices_contact,notices_email,source_document,source_clause,valid_from,valid_to,version\n"
    "7335_02891,Trentcombe Fund Investors LLC,0.0075,Invested Capital,1.0,45,Kevin Gu,kevin.gu@gradient-path.com,Side letter SL-TRENTCOMBE-2026-01 (amended and restated),\"2(a), 2(b), 2(c)\",2026-07-01,,v2\n"
    "6385_54462,Vanford Investment Pte Ltd,0.01,Commitment,0.8,60,Ops,ops@vanford.example,LPA,8.1,2024-01-01,,v2\n")
ENTITY = "term,value,source_document,source_clause,valid_from\nentity,Kestrel Lammwick Co-Invest LP,LPA,1,2024-01-01\nentity_id_corvus,2254,LPA,1,2024-01-01\ncurrency,USD,LPA,1,2024-01-01\n"
CORRECTION = "Apologies, correction:\n\n  X  Management fee rate: 0.65 percent per annum, previously 0.85 percent (clause 2(a)).\n\nI said 0.75 percent per annum by mistake\n"
JULY = ("Three changes, all effective from 1 July 2026:\n"
        "  X  Management fee rate: 0.75 percent per annum, previously 0.85 percent (clause 2(a)).\n"
        "  Y  Fee basis: our share of Invested Capital rather than Commitment, from 1 July 2026 (clause 2(b)).\n"
        "  Z  Fee offset on our share: 100 percent, previously 80 percent (clause 2(c)).\n")


def item(name, content, kind="file"):
    return Item("fixture", "test", name, name, content.encode(), "", kind, {})


def graph_with_register():
    graph = Graph()
    Ingestion(graph).ingest(item("entity_terms_v1.csv", ENTITY))
    fund = next(e.key for e in graph.state.entities.values() if e.external_ids.get("corvus:legal_entity") == "2254")
    Ingestion(graph, fund_id=fund, snapshot_as_of="2026-07-01").ingest(item("terms_table_v2.csv", TERMS_V2))
    for source in graph.state.sources.values():   # the register was learned in July; ratifications are learned today
        source.recorded_at = "2026-07-06T09:14:00+00:00"
    return graph, fund


def message(graph, text, sender="kevin.gu@gradient-path.com", name="mail-1"):
    source_id = key(name)
    graph.state.sources[source_id] = Source(key=source_id, kind="email", provider="agentmail", account="test",
                                            external_id=name, revision="1", filename="message.eml", sha256=key(text),
                                            text=text, metadata={"sender": sender, "subject": "Re: amended side letter"})
    return source_id


class TermLineTests(unittest.TestCase):
    def test_correction_line_yields_rate_and_clause(self):
        lines = term_lines(CORRECTION)
        self.assertEqual([(l["field"], l["new"], l["old_stated"], l["clause"]) for l in lines],
                         [("mgmt_fee_rate_pa", "0.0065", "0.0085", "2(a)")])

    def test_july_lines_yield_three_changes_with_stated_effective_date(self):
        lines = term_lines(JULY)
        self.assertEqual([l["field"] for l in lines], ["mgmt_fee_rate_pa", "fee_basis", "fee_offset_pct"])
        self.assertEqual(lines[1]["new"], "Invested Capital")
        self.assertEqual(lines[1]["effective_stated"], "2026-07-01")
        self.assertEqual(lines[2]["new"], "1")

    def test_prose_without_labelled_lines_proposes_nothing(self):
        self.assertEqual(term_lines("We think the fee should really be lower, maybe 0.5 percent?"), [])


class ProposalTests(unittest.TestCase):
    def setUp(self):
        self.graph, self.fund = graph_with_register()

    def test_known_notices_contact_gets_one_proposal_inheriting_the_in_force_date(self):
        source = message(self.graph, CORRECTION)
        proposals = propose_for_source(self.graph, source, as_of=date(2026, 9, 6))
        self.assertEqual(len(proposals), 1)
        p = proposals[0]
        self.assertEqual((p["investor_id"], p["field"], p["old"], p["new"], p["clause"], p["status"]),
                         ("7335_02891", "mgmt_fee_rate_pa", "0.0075", "0.0065", "2(a)", "proposed"))
        self.assertEqual(p["effective_from"], "2026-07-01")
        self.assertEqual(p["investor_matched_by"], "notices_email")
        self.assertIn(p["quote"], CORRECTION)
        self.assertEqual(self.graph.state.sources[source].metadata["term_proposals"], proposals)

    def test_unknown_sender_without_a_registered_name_proposes_nothing(self):
        source = message(self.graph, CORRECTION, sender="someone@elsewhere.example")
        self.assertEqual(propose_for_source(self.graph, source, as_of=date(2026, 9, 6)), [])

    def test_registered_name_in_text_resolves_when_sender_is_unknown(self):
        source = message(self.graph, "Re Vanford Investment Pte Ltd:\n Management fee rate: 0.9 percent per annum (clause 8.1).\n",
                         sender="someone@elsewhere.example")
        proposals = propose_for_source(self.graph, source, as_of=date(2026, 9, 6))
        self.assertEqual([(p["investor_id"], p["new"], p["investor_matched_by"]) for p in proposals],
                         [("6385_54462", "0.009", "investor_name in text")])

    def test_proposing_twice_is_idempotent_and_keeps_ratified_state(self):
        source = message(self.graph, CORRECTION)
        first = propose_for_source(self.graph, source, as_of=date(2026, 9, 6))
        ratify(self.graph, source, first[0]["proposal_id"], "Kevin Gu", "confirmed by phone", recorded_on="2026-09-06")
        again = propose_for_source(self.graph, source, as_of=date(2026, 9, 6))
        self.assertEqual([p["status"] for p in again], ["ratified"])
        self.assertEqual(len(list_proposals(self.graph, "ratified")), 1)


class RatificationTests(unittest.TestCase):
    def setUp(self):
        self.graph, self.fund = graph_with_register()
        self.source = message(self.graph, CORRECTION)
        self.proposal = propose_for_source(self.graph, self.source, as_of=date(2026, 9, 6))[0]

    def rate(self, as_of, known_at=None):
        rows = terms_as_of(self.graph, self.fund, as_of, known_at)["rows"]
        return next(r["mgmt_fee_rate_pa"] for r in rows if r["investor_id"] == "7335_02891")

    def test_ratification_appends_a_row_the_register_prefers_from_the_effective_date(self):
        result = ratify(self.graph, self.source, self.proposal["proposal_id"], "Kevin Gu", "correction confirmed", recorded_on="2026-09-06")
        self.assertEqual(result["row"]["mgmt_fee_rate_pa"], "0.0065")
        self.assertEqual(result["row"]["valid_from"], "2026-07-01")
        self.assertEqual(result["row"]["source_clause"], "2(a)")
        self.assertIn("ratified 2026-09-06", result["row"]["source_document"])
        self.assertEqual(self.rate(date(2026, 9, 30)), "0.0065")
        # snapshot_as_of gates applicability: a term ratified on 6 Sep applies to runs dated on or after
        # that day (terms_as_of ranks snapshots), so an as-of before the ratification still sees the old row.
        self.assertEqual(self.rate(date(2026, 8, 1)), "0.0075")
        applied = self.graph.state.sources[result["applied_source_id"]]
        self.assertEqual(applied.metadata["ratification"]["actor"], "Kevin Gu")
        self.assertEqual(applied.metadata["snapshot_as_of"], "2026-09-06")
        # The prior row is untouched and still reachable by provenance.
        provenance = terms_as_of(self.graph, self.fund, date(2026, 9, 30))["provenance"]["7335_02891"]
        self.assertEqual(provenance, [result["applied_source_id"]])
        self.assertEqual(self.proposal["status"], "ratified")

    def test_known_at_before_the_ratification_still_returns_the_old_term(self):
        ratify(self.graph, self.source, self.proposal["proposal_id"], "Kevin Gu", "correction confirmed", recorded_on="2026-09-06")
        self.assertEqual(self.rate(date(2026, 9, 30), known_at="2026-09-01T00:00:00+00:00"), "0.0075")

    def test_other_investors_are_untouched(self):
        ratify(self.graph, self.source, self.proposal["proposal_id"], "Kevin Gu", "correction confirmed", recorded_on="2026-09-06")
        rows = terms_as_of(self.graph, self.fund, date(2026, 9, 30))["rows"]
        self.assertEqual(next(r["mgmt_fee_rate_pa"] for r in rows if r["investor_id"] == "6385_54462"), "0.01")
        self.assertEqual(len(rows), 2)

    def test_ratification_needs_actor_and_reason_and_an_open_proposal(self):
        with self.assertRaises(ValueError):
            ratify(self.graph, self.source, self.proposal["proposal_id"], "", "reason")
        with self.assertRaises(ValueError):
            ratify(self.graph, self.source, self.proposal["proposal_id"], "Kevin Gu", "")
        ratify(self.graph, self.source, self.proposal["proposal_id"], "Kevin Gu", "ok", recorded_on="2026-09-06")
        with self.assertRaises(ValueError):
            ratify(self.graph, self.source, self.proposal["proposal_id"], "Kevin Gu", "again", recorded_on="2026-09-06")
        with self.assertRaises(KeyError):
            ratify(self.graph, self.source, "nope", "Kevin Gu", "ok")

    def test_same_day_re_ratification_replaces_that_days_row_without_a_conflict(self):
        ratify(self.graph, self.source, self.proposal["proposal_id"], "Kevin Gu", "first", recorded_on="2026-09-06")
        second = message(self.graph, "Correction to my correction:\n  X  Management fee rate: 0.7 percent per annum, previously 0.85 percent (clause 2(a)).\n", name="mail-2")
        proposal = propose_for_source(self.graph, second, as_of=date(2026, 9, 6))[0]
        self.assertEqual(proposal["old"], "0.0065")
        result = ratify(self.graph, second, proposal["proposal_id"], "Kevin Gu", "second", recorded_on="2026-09-06")
        self.assertEqual(self.rate(date(2026, 9, 30)), "0.007")
        self.assertEqual(len(self.graph.state.sources[result["applied_source_id"]].metadata["ratification_history"]), 1)


class ApiTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from app.main import app
        self.graph, self.fund = graph_with_register()
        self.source = message(self.graph, CORRECTION)
        self.client = TestClient(app)
        self.store_patch = patch("app.graph_api.GraphStore")
        self.store = self.store_patch.start().return_value
        self.addCleanup(self.store_patch.stop)
        self.store.load_graph.side_effect = lambda: Graph(self.graph.state.model_copy(deep=True))

        def commit(graph):
            self.graph = graph
        self.store.save_graph.side_effect = commit

    def test_propose_list_and_ratify_over_http(self):
        response = self.client.post(f"/graph/term-proposals/{self.source}/propose", json={"as_of": "2026-09-06"})
        self.assertEqual(response.status_code, 200, response.text)
        proposal = response.json()["proposals"][0]
        self.assertEqual(self.client.get("/graph/term-proposals?status=proposed").json()["proposals"][0]["proposal_id"], proposal["proposal_id"])
        response = self.client.post(f"/graph/term-proposals/{self.source}/{proposal['proposal_id']}/ratify",
                                    json={"actor": "Kevin Gu", "reason": "confirmed"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["terms_now"]["row"]["mgmt_fee_rate_pa"], "0.0065")
        self.assertEqual(self.client.get("/graph/term-proposals?status=ratified").json()["proposals"][0]["ratified_by"], "Kevin Gu")
        response = self.client.post(f"/graph/term-proposals/{self.source}/{proposal['proposal_id']}/ratify",
                                    json={"actor": "Kevin Gu", "reason": "twice"})
        self.assertEqual(response.status_code, 422)

    def test_unknown_source_is_404(self):
        self.assertEqual(self.client.post("/graph/term-proposals/nope/propose", json={}).status_code, 404)


class ConnectorPathTests(unittest.TestCase):
    """A correction reaching the user's own inbox arrives with no sender metadata."""

    def setUp(self):
        self.graph, self.fund = graph_with_register()

    def connector_mail(self, text):
        # What the Gmail connector produces: a real .eml, no "sender" in metadata.
        raw = ("From: Kevin Gu <kevin.gu@gradient-path.com>\r\n"
               "To: ops@marlbank.example\r\nSubject: Correction\r\n\r\n" + text)
        return Ingestion(self.graph).ingest(item("message.eml", raw, kind="email"))

    def test_sender_is_recovered_from_the_parsed_message(self):
        source = self.connector_mail(CORRECTION)
        self.assertEqual(sender_from_graph(self.graph, source), "kevin.gu@gradient-path.com")

    def test_connector_mail_raises_the_same_proposal_as_agent_mail(self):
        source = self.connector_mail(CORRECTION)
        proposals = propose_for_source(self.graph, source)
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]["investor_matched_by"], "notices_email")
        self.assertEqual((proposals[0]["old"], proposals[0]["new"]), ("0.0075", "0.0065"))
        self.assertEqual(proposals[0]["status"], "proposed")

    def test_it_still_proposes_nothing_the_register_cannot_place(self):
        raw = "From: attacker@elsewhere.example\r\nSubject: x\r\n\r\nManagement fee rate: 0.10 percent per annum (clause 9)."
        source = Ingestion(self.graph).ingest(item("hostile.eml", raw, kind="email"))
        self.assertEqual(propose_for_source(self.graph, source), [])


class DateArgumentTests(unittest.TestCase):
    """ratify and terms_as_of take strings; their siblings take dates. Accept both."""

    def setUp(self):
        self.graph, self.fund = graph_with_register()
        self.source = message(self.graph, CORRECTION)
        self.proposal = propose_for_source(self.graph, self.source, "kevin.gu@gradient-path.com")[0]

    def rate(self, known_at):
        rows = terms_as_of(self.graph, self.fund, date(2026, 9, 30), known_at)["rows"]
        return next(r["mgmt_fee_rate_pa"] for r in rows if r["investor_id"] == "7335_02891")

    def test_ratify_accepts_a_date_as_well_as_a_string(self):
        ratify(self.graph, self.source, self.proposal["proposal_id"], "Kevin Gu", "ok",
               recorded_on=date(2026, 9, 6))
        self.assertEqual(self.rate(None), "0.0065")

    def test_known_at_accepts_a_date_and_agrees_with_the_string_form(self):
        ratify(self.graph, self.source, self.proposal["proposal_id"], "Kevin Gu", "ok",
               recorded_on="2026-09-06")
        self.assertEqual(self.rate(date(2026, 9, 1)), "0.0075")
        self.assertEqual(self.rate("2026-09-01T00:00:00+00:00"), "0.0075")
        self.assertEqual(self.rate(date(2026, 9, 30)), "0.0065")


if __name__ == "__main__":
    unittest.main()
