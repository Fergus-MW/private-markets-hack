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

    def test_invalid_optional_model_proposal_retains_source_instead_of_422(self):
        store = CanonicalFixture()
        envelope = json.dumps({'provider':'gmail','account':'client-a','external_id':'email-2','use_gemini':True})
        with patch('app.source_api.load', return_value=(store, store.graph)), \
                patch('app.source_api.save') as save, \
                patch('app.extraction.gemini_extract', side_effect=ValueError('invalid proposal')):
            response = TestClient(app).post('/sources', data={'envelope': envelope},
                files={'file':('message.txt',b'Useful source text','text/plain')})
        self.assertEqual(response.status_code,200,response.text)
        source = store.graph.state.sources[response.json()['source_id']]
        self.assertEqual(source.text,'Useful source text')
        self.assertTrue(any('source retained for retry' in warning for warning in source.warnings))
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
