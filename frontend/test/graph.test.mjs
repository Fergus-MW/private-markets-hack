import test from 'node:test';
import assert from 'node:assert/strict';
import { createHmac, randomBytes } from 'node:crypto';
import { Readable } from 'node:stream';
import { createGraphProxy, assertion } from '../server/graph.mjs';
import { connectorId, seal } from '../server/auth.mjs';
const config={origin:'https://app.example.com',key:randomBytes(32)};
const secret='test-signing-key-'.repeat(4);
process.env.INGESTION_URL='https://ingestion.example.com';
process.env.GRAPH_IDENTITY_SECRET=secret;
const alice={kind:'connection',email:'alice@example.com',connector:connectorId('alice@example.com'),expires:Date.now()+60000};
function response(){return {writeHead(status,headers){this.status=status;this.headers=headers;},end(body){this.body=body;}};}
function request(session,method='GET',headers={}){const req=Readable.from([]);req.method=method;req.headers={cookie:session?'connection='+seal(session,config.key):'',...headers};return req;}
test('assertion binds verified tenant, route, method and expiry',()=>{
 const [payload,signature]=assertion(alice,'GET','/graph/entities',secret,100).split('.');
 assert.equal(signature,createHmac('sha256',secret).update(payload).digest('hex'));
 const claims=JSON.parse(Buffer.from(payload,'base64url'));
 assert.equal(claims.tenant,alice.connector);assert.equal(claims.actor,alice.email);assert.equal(claims.exp,160);assert.equal(claims.path,'/graph/entities');
});
test('unauthenticated and cross-origin requests never reach graph',async()=>{
 let calls=0;const proxy=createGraphProxy(config,{request:async()=>{calls++;}});
 let res=response();await proxy(request(null),res,new URL('/api/graph/entities',config.origin));assert.equal(res.status,401);
 res=response();await proxy(request(alice,'POST',{origin:'https://other.example'}),res,new URL('/api/graph/entities',config.origin));assert.equal(res.status,403);assert.equal(calls,0);
});
test('proxy replaces caller identity with session owner',async()=>{
 let sent;const proxy=createGraphProxy(config,{request:async options=>{sent=options;return {status:200,headers:new Headers({'content-type':'application/json'}),data:Buffer.from('{}')};}});
 const res=response();await proxy(request(alice,'GET',{'x-graph-identity':'forged','authorization':'forged'}),res,new URL('/api/graph/entities?kind=fund',config.origin));
 assert.equal(res.status,200);assert.equal(sent.url,'https://ingestion.example.com/graph/entities?kind=fund');assert.equal(sent.headers.Authorization,undefined);
 const claims=JSON.parse(Buffer.from(sent.headers['X-Graph-Identity'].split('.')[0],'base64url'));assert.equal(claims.tenant,alice.connector);
});
