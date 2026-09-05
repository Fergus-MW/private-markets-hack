import json,subprocess,urllib.request,urllib.error,time
url=subprocess.check_output(['terraform','-chdir=infrastructure','output','-raw','ingestion_url'],text=True).strip()
token=subprocess.check_output(['gcloud','auth','print-identity-token'],text=True).strip()
headers={'Authorization':'Bearer '+token}
try:
    urllib.request.urlopen(url+'/readyz',timeout=30)
except urllib.error.HTTPError as e:
    assert e.code in (401,403), e.code
    print('PASS: unauthenticated requests rejected', flush=True)
else:
    raise AssertionError('Service accepted unauthenticated request')
for attempt in range(10):
    try:
        with urllib.request.urlopen(urllib.request.Request(url+'/readyz',headers=headers),timeout=90) as r:
            assert json.load(r)['status']=='ready'
        break
    except urllib.error.HTTPError as e:
        if e.code != 503: raise
        print('Database readiness pending',flush=True)
        time.sleep(5)
else: raise AssertionError('Database not ready')
print('PASS: authenticated readiness and private database connection',flush=True)
boundary='private-markets-smoke-boundary'
data=(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="deployment-smoke.txt"\r\nContent-Type: text/plain\r\n\r\nTim Cook is the chief executive of Apple. Microsoft hired Satya Nadella.\r\n--{boundary}--\r\n').encode()
results=[]
for i in range(2):
    req=urllib.request.Request(url+'/documents',data=data,headers={**headers,'Content-Type':'multipart/form-data; boundary='+boundary})
    with urllib.request.urlopen(req,timeout=180) as r:
        results.append(json.load(r))
assert results[0]==results[1],results
assert results[0]['chunks'] >= 1 and 'people' not in results[0] and 'companies' not in results[0], results
with urllib.request.urlopen(urllib.request.Request(url + results[0]['context_url'], headers=headers), timeout=90) as response:
    context = json.load(response)
assert context['chunks'] and context['chunks'][0]['sources'], context
assert context['chunks'][0]['citation']['document_id'] == results[0]['document_id']
print('PASS: agent context retrieval and source citations', flush=True)
print('PASS: deployed document ingestion and duplicate retry', json.dumps(results[0]), flush=True)
print('Service:',url)
