import unittest
from types import SimpleNamespace as NS
from app.entities import extract, normalize

class FakeNLP:
    def pipe(self, texts):
        for text in texts:
            yield NS(ents=[NS(text=text, label_="PERSON" if "Alice" in text else "ORG", start_char=0, end_char=len(text))])

class EntityTests(unittest.TestCase):
    def test_unicode_and_whitespace(self):
        self.assertEqual(normalize("  ＡＣＭＥ  Ltd "), "acme ltd")

    def test_people_are_not_merged_across_documents(self):
        a, _ = extract([{"text": "Alice Smith"}], FakeNLP(), "doc1")
        b, _ = extract([{"text": "Alice Smith"}], FakeNLP(), "doc2")
        self.assertNotEqual(a[0]["key"], b[0]["key"])

    def test_company_aliases_share_candidate_and_keep_mentions(self):
        entities, mentions = extract([{"text": "ACME"}, {"text": "acme"}], FakeNLP(), "doc1")
        self.assertEqual(len(entities), 1)
        self.assertEqual(entities[0]["aliases"], ["ACME", "acme"])
        self.assertEqual(len(mentions), 2)
        self.assertNotEqual(mentions[0]["key"], mentions[1]["key"])
        self.assertEqual(mentions[0]["start"], 0)

    def test_retry_is_deterministic(self):
        args = ([{"text": "ACME", "page_number": 2}], FakeNLP(), "doc1")
        self.assertEqual(extract(*args), extract(*args))

if __name__ == "__main__":
    unittest.main()
