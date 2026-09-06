import base64
import hashlib
import hmac
import json
import os
import time
import unittest
import uuid
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.identity import current_identity, verify
from app.main import app
from app.store import Store, GraphStore
from app.project_store import ProjectStore, project_database
from app.graph import Graph
from app.connectors import Item
from app.extraction import Ingestion
from app.projects import materialize

SECRET = 'test-identity-key-' + 'a' * 40


def assertion(tenant='alice', path='/graph/entities', method='GET', **extra):
    now = int(time.time())
    claims = dict(tenant=tenant, actor=tenant, kind='user', aud='knowledge-graph',
                  iat=now, exp=now+60, method=method, path=path)
    claims.update(extra)
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip('=')
    return payload + '.' + hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()


class IdentityTests(unittest.TestCase):
    def test_unsigned_forged_expired_and_wrong_route_fail_closed(self):
        with patch.dict(os.environ, GRAPH_MULTI_USER='true', GRAPH_IDENTITY_SECRET=SECRET):
            client=TestClient(app)
            for token in ['', assertion()+'x', assertion(exp=0), assertion(path='/sources')]:
                result=client.get('/graph/entities',headers={'X-Graph-Identity':token,'X-User-ID':'alice'})
                self.assertEqual(result.status_code,401)
            self.assertEqual(client.get('/healthz').status_code,200)
            with self.assertRaises(ValueError):
                verify(assertion(kind='connector'),'GET','/graph/entities')
            with self.assertRaises(ValueError):
                GraphStore()

    def test_identity_is_reset_between_requests(self):
        observed=[]
        def load():
            observed.append(current_identity.get()['tenant'])
            return None,Graph()
        with patch.dict(os.environ, GRAPH_MULTI_USER='true', GRAPH_IDENTITY_SECRET=SECRET), patch('app.graph_api.load',side_effect=load):
            client=TestClient(app)
            for user in ('alice','bob','alice'):
                self.assertEqual(client.get('/graph/entities',headers={'X-Graph-Identity':assertion(user)}).status_code,200)
            self.assertEqual(client.get('/graph/entities').status_code,401)
        self.assertEqual(observed,['alice','bob','alice'])
        self.assertIsNone(current_identity.get())

    def test_upload_account_and_ratification_actor_cannot_override_identity(self):
        from test_projects import CanonicalFixture
        store=CanonicalFixture()
        with patch.dict(os.environ, GRAPH_MULTI_USER='true', GRAPH_IDENTITY_SECRET=SECRET):
            client=TestClient(app)
            with patch('app.source_api.load',return_value=(store,store.graph)),patch('app.source_api.save'):
                result=client.post('/sources',headers={'X-Graph-Identity':assertion('alice','/sources','POST')},
                    data={'envelope':json.dumps({'provider':'fixture','account':'bob','external_id':'same'})},
                    files={'file':('source.txt',b'input','text/plain')})
                self.assertEqual(result.status_code,200,result.text)
                self.assertEqual(store.graph.state.sources[result.json()['source_id']].account,'alice')
            path='/projects/'+'a'*64+'/ratifications'
            with patch('app.project_api.local_store'),patch('app.project_api.ratify',return_value={'ok':True}) as ratify:
                response=client.post(path,headers={'X-Graph-Identity':assertion('alice',path,'POST')},
                    json={'artifact_id':'artifact','actor':'bob','evidence_ids':['source'],'reason':'reviewed'})
                self.assertEqual(response.status_code,200)
                self.assertEqual(ratify.call_args.args[2],'alice')


@unittest.skipUnless(os.environ.get('KG_PROJECT_TESTS') == '1','Requires local SurrealDB')
class UserDatabaseTests(unittest.TestCase):
    def test_identical_keys_are_isolated_for_graphs_blobs_and_projects(self):
        root=Store(namespace='projects',database='catalog')
        root.query('DEFINE NAMESPACE IF NOT EXISTS projects;')
        databases=[]
        env={'SURREAL_PROJECT_SECRET':'test-'+uuid.uuid4().hex,
             'SURREAL_PROJECT_ADMIN_USER':os.environ.get('SURREAL_USER','root'),
             'SURREAL_PROJECT_ADMIN_PASSWORD':os.environ['SURREAL_PASSWORD'],
             'SURREAL_PROJECT_ADMIN_AUTH_LEVEL':os.environ.get('SURREAL_AUTH_LEVEL','root'),
             'GRAPH_MULTI_USER':'true','GRAPH_IDENTITY_SECRET':SECRET}
        users=['alice_'+uuid.uuid4().hex,'bob_'+uuid.uuid4().hex]
        stores=[]
        try:
            with patch.dict(os.environ,env):
                for user in users:
                    token=current_identity.set({'tenant':user,'actor':user,'kind':'user'})
                    try:
                        store=GraphStore();stores.append(store);databases.append(store.database)
                        graph=store.load_graph()
                        self.assertEqual(len(graph.state.sources),0)
                        source=Ingestion(graph,store).ingest(Item('fixture','same','same','same.txt',b'identical input'))
                        fund=graph.upsert('fund','Fund',source)
                        company=graph.upsert('company','Manager',source)
                        project=graph.upsert('project','Q2',source,fund_id=fund,management_company_id=company,quarter='2026-Q2',workflow_type='terms')
                        graph.edge(source,'part_of',project,source)
                        store.save_graph(graph)
                        store.save_source_bytes('collision',user.encode())
                        databases.append(project_database(project))
                        materialize(store,project,[source])
                        ProjectStore(project).bundle(nodes=[{'key':'private', 'owner':user}])
                        self.assertEqual(ProjectStore(project).manifest()['project_id'],project)
                    finally:
                        current_identity.reset(token)
                self.assertEqual(len(set(databases)),4)
                for index,store in enumerate(stores):
                    self.assertEqual(store.get_source_bytes('collision'),users[index].encode())
                # A credential valid in Alice's DB must not access Bob's DB.
                attacker=Store(namespace='projects',database=stores[1].database,user=stores[0].user,password=stores[0].password,auth_level='database')
                with self.assertRaises(Exception):
                    attacker.get_source_bytes('collision')
                client=TestClient(app)
                path=f'/projects/{project}/graph'
                self.assertEqual(client.get(path,headers={'X-Graph-Identity':assertion('unknown-user',path)}).status_code,503)
                for user in users:
                    response=client.get(path,headers={'X-Graph-Identity':assertion(user,path)})
                    self.assertEqual(response.status_code,200)
                    private=next(r for r in response.json()['records'] if r['key']=='private')
                    self.assertEqual(private['owner'],user)
        finally:
            for database in databases:
                root.query('REMOVE DATABASE IF EXISTS '+database+';')
