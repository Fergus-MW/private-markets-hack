import unittest
from types import SimpleNamespace as NS
from app.context import context_page, prepare_context

class Metadata:
    def __init__(self, **values):
        self.__dict__.update(values)
    def to_dict(self):
        return self.__dict__.copy()

class ContextTests(unittest.TestCase):
    def test_budget_pagination_never_silently_drops_chunks(self):
        chunks = [{"text": "a" * 3000, "chunk_id": str(i)} for i in range(5)]
        doc = {"key": "doc", "filename": "report", "pipeline_version": "v2", "chunks": chunks}
        seen, offset = [], 0
        while offset is not None:
            page = context_page(doc, offset, max_characters=4000)
            self.assertLessEqual(page["characters"], 4000)
            seen.extend(c["chunk_id"] for c in page["chunks"])
            offset = page["next_offset"]
        self.assertEqual(seen, [str(i) for i in range(5)])

    def test_sources_preserve_pages_and_sheet_names_without_temp_paths(self):
        parsed = [NS(text="Revenue 100", category="Table", metadata=Metadata(
            page_number=2, page_name="Accounts", filename="input.xlsx", file_directory="/tmp/private",
            text_as_html="<table><tr><td>Revenue</td><td>100</td></tr></table>"))]
        def chunker(elements, **options):
            return [NS(text=elements[0].text, category="Table", metadata=NS(orig_elements=elements))]
        first = prepare_context(parsed, "doc", "annual.xlsx", chunker)
        second = prepare_context(parsed, "doc", "annual.xlsx", chunker)
        self.assertEqual(first, second)
        element, chunk = first[0][0], first[1][0]
        self.assertNotIn("file_directory", element["metadata"])
        self.assertNotIn("filename", element["metadata"])
        self.assertIn("text_as_html", element["metadata"])
        self.assertEqual(chunk["sources"][0]["page_name"], "Accounts")
        self.assertEqual(chunk["sources"][0]["element_id"], element["element_id"])
        self.assertEqual(chunk["citation"]["filename"], "annual.xlsx")

    def test_offset_past_end_is_terminal(self):
        doc = {"key": "doc", "filename": "report", "pipeline_version": "v2", "chunks": []}
        self.assertIsNone(context_page(doc, offset=10)["next_offset"])

if __name__ == "__main__":
    unittest.main()
