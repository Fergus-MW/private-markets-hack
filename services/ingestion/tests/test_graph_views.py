import unittest
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.graph import Graph, Source, key
from app.graph_api import router
from app.identity import IdentityMiddleware, current_identity
from test_identity import assertion, SECRET


class GraphViewTests(unittest.TestCase):
    def setUp(self):
        import os
        self.env = patch.dict(os.environ, GRAPH_MULTI_USER='true', GRAPH_IDENTITY_SECRET=SECRET)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.app = FastAPI()
        self.app.add_middleware(IdentityMiddleware)
        self.app.include_router(router)
        self.client = TestClient(self.app)
        self.graph = Graph()
        sid = key('source')
        self.graph.state.sources[sid] = Source(key=sid, kind='file', provider='fixture', account='alice',
            external_id='s', revision='1', filename='Report.txt', sha256=key('body'), text='PRIVATE BODY')
        self.fund = self.graph.upsert('fund', 'Fund A', sid)
        manager = self.graph.upsert('company', 'Manager', sid)
        self.project = self.graph.upsert('project', 'Quarterly review', sid, fund_id=self.fund,
            management_company_id=manager, quarter='2026-Q3', workflow_type='terms')
        self.graph.edge(manager, 'manages', self.fund, sid)

    def get(self, path, user='alice'):
        return self.client.get(path, headers={'X-Graph-Identity': assertion(user, path)})

    def test_catalog_and_payload_follow_request_identity(self):
        def load():
            return None, self.graph if current_identity.get()['tenant'] == 'alice' else Graph()
        with patch('app.graph_api.load', side_effect=load):
            alice = self.get('/graph/views').json()['graphs']
            self.assertEqual([item['id'] for item in alice], ['workspace', self.project])
            self.assertEqual(len(self.get('/graph/views', 'bob').json()['graphs']), 1)
            payload = self.get('/graph/views/workspace').json()
            self.assertEqual(len(payload['nodes']), 4)
            self.assertEqual(len(payload['edges']), 1)
            self.assertNotIn('PRIVATE BODY', str(payload))
            self.assertNotIn('account', str(payload))
            self.assertEqual(self.get('/graph/views/workspace', 'bob').json()['nodes'], [])
            self.assertEqual(self.get('/graph/views/' + self.project, 'bob').status_code, 404)
        self.assertEqual(self.client.get('/graph/views').status_code, 401)
        self.assertEqual(self.client.get('/graph/views/workspace').status_code, 401)

    def test_project_payload_includes_workflow_nodes_but_not_blobs_or_credentials(self):
        records = {'node': [{'key': 'source', 'kind': 'file', 'filename': 'Evidence', 'text': 'PRIVATE BODY'}],
                   'artifact': [{'key': 'artifact', 'kind': 'file', 'filename': 'Result', 'base64': 'SECRET BLOB'}],
                   'run': [{'key': 'run', 'kind': 'run', 'claim_token': 'SECRET TOKEN'}], 'decision': [],
                   'link': [{'key': 'link', 'subject': 'artifact', 'object': 'source', 'predicate': 'derived_from'},
                            {'key': 'missing', 'subject': 'absent', 'object': 'source', 'predicate': 'copied'}]}
        with patch('app.graph_api.load', return_value=(None, self.graph)), \
             patch('app.project_api.local_store'), \
             patch('app.projects.all_records', side_effect=lambda store, table: records[table]):
            response = self.get('/graph/views/' + self.project)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()['nodes']), 3)
        self.assertEqual(len(response.json()['edges']), 1)
        for private in ('PRIVATE BODY', 'SECRET BLOB', 'SECRET TOKEN'):
            self.assertNotIn(private, response.text)

    def test_project_graph_exists_before_the_first_workflow(self):
        with patch('app.graph_api.load', return_value=(None, self.graph)), \
             patch('app.project_api.local_store', side_effect=HTTPException(404, 'not materialized')):
            response = self.get('/graph/views/' + self.project)
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.project, {node['id'] for node in response.json()['nodes']})
        self.assertIn(self.fund, {node['id'] for node in response.json()['nodes']})
        self.assertNotIn('PRIVATE BODY', response.text)
