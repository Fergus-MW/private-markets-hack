import unittest

from app.graph import Graph, Source, key
from app.mail_ingest import related_projects, sender_entity


def fixture_source(graph, name="source"):
    source_id = key(name)
    graph.state.sources[source_id] = Source(key=source_id, kind="file", provider="fixture",
        account="test", external_id=name, revision="1", filename=name, sha256=key(name), text=name)
    return source_id


def graph_with():
    graph = Graph()
    source = fixture_source(graph)
    graph.upsert("person", "Ada Client", source, emails=["ada@client.com"])
    graph.upsert("company", "Client Co", source, domains=["client.com"])
    return graph


class SenderTests(unittest.TestCase):
    def setUp(self):
        self.graph = graph_with()

    def test_known_person_matches_on_exact_email_case_insensitively(self):
        self.assertEqual(sender_entity(self.graph, "ADA@Client.com").name, "Ada Client")

    def test_unknown_person_falls_back_to_the_company_domain(self):
        self.assertEqual(sender_entity(self.graph, "new.hire@client.com").kind, "company")

    def test_unrelated_domain_is_never_known(self):
        self.assertIsNone(sender_entity(self.graph, "attacker@elsewhere.com"))

    def test_malformed_addresses_are_rejected_before_matching(self):
        for address in ("", "not-an-address", "a@b", "ada@client.com extra"):
            self.assertIsNone(sender_entity(self.graph, address))

    def test_merged_entities_never_match(self):
        for entity in self.graph.state.entities.values():
            entity.merged_into = "other"
        self.assertIsNone(sender_entity(self.graph, "ada@client.com"))


class ProjectMatchTests(unittest.TestCase):
    def setUp(self):
        self.graph = Graph()
        self.source = fixture_source(self.graph)
        self.mail = fixture_source(self.graph, "mail")
        self.attached = fixture_source(self.graph, "attachment")
        self.fund = self.graph.upsert("fund", "Fund A", self.source)
        self.company = self.graph.upsert("company", "Manager", self.source)
        self.project = self.graph.upsert("project", "2026-Q2 loader", self.source, fund_id=self.fund,
                                         management_company_id=self.company, quarter="2026-Q2",
                                         workflow_type="loader")

    def test_direct_part_of_edge_wins(self):
        self.graph.edge(self.mail, "part_of", self.project, self.source)
        self.graph.edge(self.mail, "mentions", self.fund, self.source)
        self.assertEqual(related_projects(self.graph, {self.mail}), [(self.project, "part_of")])

    def test_mentioned_fund_matches_in_progress_projects(self):
        self.graph.edge(self.mail, "mentions", self.fund, self.source)
        self.assertEqual(related_projects(self.graph, {self.mail}), [(self.project, "mentions")])

    def test_completed_projects_are_never_auto_refreshed(self):
        self.graph.state.entities[self.project].status = "completed"
        self.graph.edge(self.mail, "mentions", self.fund, self.source)
        self.assertEqual(related_projects(self.graph, {self.mail}), [])

    def test_an_attachment_mention_counts_for_the_message(self):
        self.graph.edge(self.attached, "mentions", self.fund, self.source)
        self.assertEqual(related_projects(self.graph, {self.mail}), [])
        self.assertEqual(related_projects(self.graph, {self.mail, self.attached}), [(self.project, "mentions")])

    def test_unrelated_mention_matches_nothing(self):
        other = self.graph.upsert("fund", "Fund B", self.source)
        self.graph.edge(self.mail, "mentions", other, self.source)
        self.assertEqual(related_projects(self.graph, {self.mail}), [])


if __name__ == "__main__":
    unittest.main()
