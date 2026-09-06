import io
import json
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
from test_projects import CanonicalFixture

class SourceApiTests(unittest.TestCase):
    def test_source_upload_retains_bytes_and_graph_identity(self):
        store = CanonicalFixture()
        with patch('app.source_api.load', return_value=(store, store.graph)), patch('app.source_api.save') as save:
            response = TestClient(app).post('/sources', data={'envelope': json.dumps({'provider':'gmail','account':'client-a','external_id':'email-1'})}, files={'file':('message.eml',b'From: Dana <dana@example.com>\r\nSubject: test\r\n\r\nhello','message/rfc822')})
        self.assertEqual(response.status_code,200,response.text)
        source = response.json()['source_id']
        self.assertIn(source,store.blobs)
        self.assertEqual(store.graph.state.sources[source].account,'client-a')
        save.assert_called_once()

    def test_large_workbook_retains_original_and_defers_extraction(self):
        from openpyxl import Workbook
        from app.connectors import Item
        from app.extraction import Ingestion
        book=Workbook();book.active.append(['column']);book.active.append(['long value'])
        buffer=io.BytesIO();book.save(buffer)
        store=CanonicalFixture()
        with patch('app.extraction.MAX_TEXT',200), patch('app.extraction.gemini_extract') as model:
            book.active.append(['x'*300]);buffer=io.BytesIO();book.save(buffer)
            source=Ingestion(store.graph,store,use_gemini=True).ingest(Item('fixture','a','x','x.xlsx',buffer.getvalue()))
        self.assertTrue(store.blobs)
        self.assertTrue(any('deferred_to_project' in w for w in store.graph.state.sources[source].warnings))
        model.assert_not_called()
