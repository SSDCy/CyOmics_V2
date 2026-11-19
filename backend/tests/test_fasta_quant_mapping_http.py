"""One finite new target. Original parsers/core/Authority/TX; explicit memory SQL.
No old target invocation, actual DB/RLS/listener/native/Golden proof.
"""
from pathlib import Path
import sys
BACKEND=Path(__file__).resolve().parents[1];sys.path.insert(0,str(BACKEND))
import asyncio,copy,hashlib,json,socket,subprocess,time,unittest
from datetime import datetime,timezone
from uuid import UUID,uuid4
from unittest.mock import patch
from contextlib import ExitStack
import httpx
from psycopg.pq import TransactionStatus as T
from test_fasta_private_http import DB as FastaDB,Connection as FastaConnection,Cursor,A,B,P,P2,RAW,token
from cyomics_projects import import_persistence as ip,fasta_intake_contract as fasta,fasta_quant_mapping as core
from cyomics_projects import fasta_quant_mapping_repository as repo
from cyomics_projects.app import create_app,entry_budget
from cyomics_projects.settings import Fault
from cyomics_projects.upload_repository import UploadOutcome

QUANT=b'Protein IDs\tLFQ intensity A\ndup; missing ;dup\t10\nnone\t20\n;\t0\nempty\t\n'
class DB(FastaDB):
 def __init__(self):
  super().__init__();self.raws={};self.datasets={};self.mappings={};self.mapping_counts=None;self.insert_fail=False
 async def getconn(self):return Connection(self)
class Connection(FastaConnection):
 async def execute(self,sql,params=()):
  db=self.db
  if sql.startswith('BEGIN'):self.mapping_snapshot=copy.copy(db.mappings)
  if sql=='ROLLBACK':db.mappings=self.mapping_snapshot
  if 'FROM cyomics_app.fasta_quant_mapping_versions' in sql:
   db.events.append(sql);pid=str(params[0])
   if sql.startswith('SELECT count(*)'):
    pid,pid2,actor=map(str,params);assert self.actor==actor and pid==pid2
    if db.mapping_counts is not None:return Cursor([db.mapping_counts])
    rows=[r for r in db.mappings.values() if db.projects[str(r[1])]==actor];here=[r for r in rows if str(r[1])==pid]
    return Cursor([(len(rows),sum(r[11] for r in rows),len(here),sum(r[11] for r in here))])
   assert db.projects[pid]==self.actor
   if 'dataset_version_id=%s' in sql:
    values=tuple(map(str,params));rows=[r for r in db.mappings.values() if (str(r[1]),str(r[3]),str(r[4]),str(r[5]),r[6])==values]
   else:rows=[r for r in db.mappings.values() if str(r[1])==pid and str(r[0])==str(params[1])]
   assert len(rows)<=1;return Cursor(rows)
  if sql.startswith('INSERT INTO cyomics_app.fasta_quant_mapping_versions'):
   db.events.append(sql);assert self.writing and self.actor==str(params[2]) and db.projects[str(params[1])]==self.actor
   if db.insert_fail:db.insert_fail=False;raise RuntimeError('synthetic insert failure')
   key=str(params[0]);assert key not in db.mappings;db.mappings[key]=tuple(params);return Cursor([tuple(params)])
  if sql.startswith('SELECT file_id FROM cyomics_app.dataset_versions'):
   db.events.append(sql);pid,did=map(str,params);assert db.projects[pid]==self.actor;row=db.datasets.get(did);return Cursor([(row[2],)] if row and str(row[1])==pid else [])
  if sql.startswith('SELECT file_id,project_id,format,bytes,sha256,status'):
   db.events.append(sql);pid,fid=map(str,params);assert db.projects[pid]==self.actor;row=db.raws.get(fid)
   return Cursor([row if ',content' in sql else row[:6]] if row and str(row[1])==pid else [])
  if sql.startswith('SELECT '+ip.CONTRACT_VERSION):raise AssertionError('unexpected dynamic SQL')
  if sql.startswith('SELECT dataset_version_id,project_id,file_id,parser_contract'):
   db.events.append(sql);pid,fid,did=map(str,params);assert db.projects[pid]==self.actor;row=db.datasets.get(did);return Cursor([row] if row and str(row[1])==pid and str(row[2])==fid else [])
  return await super().execute(sql,params)

def seed(db,project=P,quant=QUANT):
 now=datetime.now(timezone.utc);fid=str(uuid4());raw=ip.StoredRaw(fid,project,'proteinGroups_txt',len(quant),hashlib.sha256(quant).hexdigest(),'stored',quant)
 source=ip.trusted_source(raw,project,fid,lambda:None);config=ip.canonical_json(ip.configuration(),ip.CONFIG_MAX_BYTES);did=ip.version_key(fid,config)
 candidate=ip.build_candidate(raw,source,config,did,lambda:None,lambda:False)
 db.raws[fid]=(UUID(fid),UUID(project),'proteinGroups_txt',raw.bytes,raw.sha256,'stored',quant)
 db.datasets[did]=(UUID(did),UUID(project),UUID(fid),ip.CONTRACT_VERSION,candidate.config_sha256,config,raw.bytes,raw.sha256,'raw_backed',False,candidate.report_json,candidate.report_sha256,now)
 sid=str(uuid4());rq=str(uuid4());dto=dict(source_id=sid,project_id=project,request_id=rq,filename='synthetic.fa',format='protein_fasta',bytes=len(RAW),sha256=hashlib.sha256(RAW).hexdigest(),status='stored',createdAt=repo.stamp(now))
 stored=fasta.source_from_json(core._json(dto),RAW);index=fasta.build_candidate(stored,expected_project_id=project,expected_source_id=sid,config_json=fasta.CONFIG_JSON,control=fasta.ParseControl(checkpoint=lambda:None))
 db.sources[(project,rq)]=(UUID(sid),UUID(project),UUID(rq),'synthetic.fa','protein_fasta',len(RAW),stored.sha256,'stored',now,RAW)
 db.indexes[sid]=(UUID(index.index_version_id),UUID(project),UUID(sid),len(RAW),stored.sha256,index.config_sha256,index.index_manifest_sha256,core._json(fasta.candidate_snapshot(index)).decode(),now)
 return dict(dataset_version_id=did,fasta_source_id=sid,fasta_index_version_id=index.index_version_id)

class MappingTarget(unittest.IsolatedAsyncioTestCase):
 async def test_one_finite_mapping_HTTP_core_target(self):
  db=DB();values=seed(db);apps=[];clients=[];groups=[]
  async def auth(request):
   import base64
   actor=json.loads(base64.urlsafe_b64decode(request.headers['authorization'].split('.')[1]+'=='))['sub']
   return httpx.Response(200,json=dict(id=actor,role='authenticated',aud='authenticated',is_anonymous=False,email='synthetic@example.invalid',email_confirmed_at='2026-01-01T00:00:00Z'))
  def client(enabled=True):
   app=create_app(auth_transport=httpx.MockTransport(auth),pool=db,anon_key_loader=lambda:token(role='anon'),fasta_mapping_enabled=enabled);apps.append(app)
   c=httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:57430');clients.append(c);return c,app
  base='/api/v1/projects/'+P+'/fasta-quant-mappings'
  headers=dict(authorization='Bearer '+token(),origin='http://127.0.0.1:4206',**{'sec-fetch-site':'same-origin','x-cyomics-request':'1','content-type':'application/json'})
  lookup=lambda v:base+'/lookup?'+'&'.join(k+'='+v[k] for k in repo.INPUT_KEYS)
  async def save(c,v=values,**kwargs):return await c.post(base,content=core._json(v),headers=headers,**kwargs)
  with ExitStack() as stack:
   network=stack.enter_context(patch.object(socket.socket,'connect',side_effect=AssertionError('network forbidden')))
   native=stack.enter_context(patch.object(subprocess,'Popen',side_effect=AssertionError('native forbidden')))
   asyncnative=stack.enter_context(patch('asyncio.create_subprocess_exec',side_effect=AssertionError('native forbidden')))
   fa=stack.enter_context(patch.object(fasta,'parse_fasta',wraps=fasta.parse_fasta));quant=stack.enter_context(patch.object(repo,'parse_protein_groups',wraps=repo.parse_protein_groups));mapping=stack.enter_context(patch.object(core,'map_quant_to_fasta',wraps=core.map_quant_to_fasta))
   try:
    off,offapp=client(False);self.assertIsNone(offapp.state.fasta_mapping.repository);self.assertEqual((await off.get(lookup(values),headers=headers)).status_code,503);self.assertEqual(len(db.events),0)
    with self.assertRaises(Exception):client(1)
    c,app=client();response=await save(c);self.assertEqual(response.status_code,201,response.text);meta=response.json();mid=meta['mapping_version_id'];self.assertEqual((fa.call_count,quant.call_count,mapping.call_count),(1,1,1));self.assertEqual(len(db.mappings),1)
    self.assertEqual(set(meta),{'schemaVersion','contract','mapping_version_id','project_id','created_by','algorithm','binding_sha256','inputs','summary','result','status','createdAt'})
    self.assertEqual({k:meta['summary'][k] for k in ('complete','partial','unmatched','empty','row_count','member_occurrences')},dict(complete=1,partial=1,unmatched=1,empty=1,row_count=4,member_occurrences=5))
    self.assertEqual(meta['summary']['matched_member_occurrences'],3);self.assertEqual(meta['summary']['match_record_references'],5)
    reopened,newapp=client()
    for path in [lookup(values),base+'/'+mid]:
     response=await reopened.get(path,headers=headers);self.assertEqual(response.status_code,200,response.text);self.assertEqual(response.json(),meta)
    response=await reopened.get(base+'/'+mid+'/content',headers=headers);self.assertEqual(response.status_code,200,response.text);full=response.content;doc=response.json()
    self.assertEqual(len(full),meta['result']['bytes']);self.assertEqual(hashlib.sha256(full).hexdigest(),meta['result']['sha256']);self.assertEqual(response.headers['x-cyomics-file-sha256'],meta['result']['sha256']);self.assertEqual(response.headers['content-length'],str(len(full)));self.assertEqual(response.headers['content-type'],'application/json');self.assertIn('cyomics_fasta_quant_mapping_'+mid+'.json',response.headers['content-disposition'])
    self.assertEqual(set(doc['fasta_records']),{'1','2','3'});self.assertEqual(doc['rows'][0]['members'][0]['fasta_record_ordinals'],[1,2]);self.assertEqual(doc['rows'][0]['members'][1]['segment_ordinal'],1);self.assertTrue(doc['rows'][0]['has_invalid_canonical_match']);self.assertTrue(doc['rows'][0]['has_duplicate_fasta_id']);self.assertTrue(all(doc[k] is False for k in ('annotation_ready','owner_authorized','published')))
    response=await save(reopened);self.assertEqual(response.status_code,200,response.text);self.assertEqual(response.json(),meta);self.assertEqual((fa.call_count,quant.call_count,mapping.call_count),(1,1,1));self.assertEqual(len(db.mappings),1)
    for key,value in [('cache-control','no-store'),('x-content-type-options','nosniff'),('referrer-policy','no-referrer')]:self.assertEqual(response.headers[key],value)
    groups.append('real original full quant/report + one genuine FASTA rebuild + unchanged core, all rows/members/evidence; save201/repeat200 and refreshed GET lookup/version/full bytes no reparse/recompute')
    counts=(fa.call_count,quant.call_count,mapping.call_count);foreign=dict(headers,authorization='Bearer '+token(B))
    for path in [lookup(values),base+'/'+mid,base+'/'+mid+'/content']:self.assertEqual((await c.get(path,headers=foreign)).status_code,404)
    self.assertEqual((await c.post(base,content=core._json(values),headers=foreign)).status_code,404);self.assertEqual(counts,(fa.call_count,quant.call_count,mapping.call_count))
    anonymous=dict(headers);anonymous.pop('authorization');self.assertEqual((await c.get(lookup(values),headers=anonymous)).status_code,401)
    for path in [lookup(values)+'&dataset_version_id='+values['dataset_version_id'],lookup(values).replace('dataset_version_id=','x='),base+'/'+mid+'?x=1']:
     self.assertEqual((await c.get(path,headers=headers)).status_code,400)
    self.assertEqual((await c.get(base+'/'+mid+'/',headers=headers)).status_code,404);self.assertEqual((await c.get((base+'/'+mid).replace('fasta-quant','%66asta-quant'),headers=headers)).status_code,404)
    self.assertEqual((await c.put(base,headers=headers)).status_code,405)
    self.assertEqual((await c.request('GET',base+'/'+mid,content=b'x',headers=headers)).status_code,400)
    for raw in [b'{}',core._json(dict(values,verified=True)),b'{"dataset_version_id":"'+values['dataset_version_id'].encode()+b'","dataset_version_id":"x"}']:
     self.assertEqual((await c.post(base,content=raw,headers=headers)).status_code,400)
    self.assertEqual((await c.post(base,content=b'x'*513,headers=headers)).status_code,413)
    forged=dict(headers,**{'x-owner-id':A});self.assertEqual((await c.get(lookup(values),headers=forged)).status_code,403)
    groups.append('original Authority and Owner before source parse; exact triple/duplicate body/query/no verified bool; aliases/method/body/512B/forged Owner rejected')
    # Persisted tampering must fail even if superficial bytes/hash metadata agree.
    row=db.mappings[mid];bad=copy.deepcopy(doc);bad['rows'][0]['members'][0]['fasta_record_ordinals']=[1];badraw=core._json(bad)
    broken=list(row);broken[10]=badraw;broken[11]=len(badraw);broken[12]=hashlib.sha256(badraw).hexdigest();db.mappings[mid]=tuple(broken)
    self.assertEqual((await c.get(base+'/'+mid+'/content',headers=headers)).json(),{'error':{'code':'fasta_mapping_persisted_invalid'}});db.mappings[mid]=row
    # Each absent-input attempt is distinct and bounded. Existing bypasses quota/rate.
    v2=seed(db);db.mapping_counts=(16,0,8,0);self.assertEqual((await save(c,v2)).status_code,413);db.mapping_counts=None
    app.state.fasta_mapping.attempts[A]=(time.monotonic()+600,10);self.assertEqual((await save(c)).status_code,200);self.assertEqual((await save(c,v2)).status_code,429);app.state.fasta_mapping.attempts.clear()
    app.state.fasta_mapping.compute_active=1;self.assertEqual((await save(c,v2)).status_code,429);app.state.fasta_mapping.compute_active=0
    db.insert_fail=True;self.assertEqual((await save(c,v2)).status_code,503);self.assertEqual(len(db.mappings),1)
    # Commit happened but acknowledgement lost: original outcome504; only GET truth recovery.
    db.unknown_commit=True;response=await save(c,v2);self.assertEqual(response.status_code,504,response.text);self.assertEqual(response.json(),{'error':{'code':'fasta_mapping_result_unknown'}});self.assertEqual(len(db.mappings),2)
    counts=(fa.call_count,quant.call_count,mapping.call_count);response=await c.get(lookup(v2),headers=headers);self.assertEqual(response.status_code,200,response.text);self.assertEqual(counts,(fa.call_count,quant.call_count,mapping.call_count))
    groups.append('full coherence rejects tampered evidence; append-only quota/rate/admission; original TX insert failure rollback, commit acknowledgement unknown504 then read-only lookup actual stored truth')
    v3=seed(db);fid=next(str(r[2]) for r in db.datasets.values() if str(r[0])==v3['dataset_version_id']);rawrow=db.raws[fid];db.raws[fid]=rawrow[:6]+(rawrow[6]+b'x',)
    self.assertEqual((await save(c,v3)).json(),{'error':{'code':'fasta_mapping_source_integrity_failed'}});db.raws[fid]=rawrow
    # Deterministic phase-fault injection checks fixed504 preservation, not actual CPU timing.
    with patch.object(repo.Phase,'check',side_effect=Fault(504,'fasta_mapping_timeout')):
     response=await save(c,v3);self.assertEqual(response.status_code,504,response.text);self.assertEqual(response.json()['error']['code'],'fasta_mapping_timeout')
    wrong=dict(v3,fasta_index_version_id=str(uuid4()));self.assertEqual((await save(c,wrong)).status_code,404)
    groups.append('original raw SHA/length integrity; timeout fault preservation and exact saved index binding, no synthetic verified proof')
    for kind,path,method,expected in [('save',base,'POST',49),('lookup',lookup(values),'GET',8),('content',base+'/'+mid+'/content','GET',49)]:
     path,_,query=path.partition('?');scope=dict(type='http',path=path,raw_path=path.encode(),method=method,query_string=query.encode(),headers=[]);self.assertGreater(entry_budget(scope).remaining(),expected)
    self.assertEqual(fasta.ParseControl().max_cpu_seconds,8.0);self.assertFalse(any(sql.startswith(('UPDATE','DELETE','CREATE','ALTER','DROP')) for sql in db.events));self.assertEqual((network.call_count,native.call_count,asyncnative.call_count),(0,0,0))
    print(json.dumps(dict(target='PASS_ONE_NEW_FINITE_MAPPING_TARGET',groups=groups,original_Authority='MockTransport',original_UploadRepository_transaction=True,SQL='explicit memory SQL, not actual PG/RLS',original_core=True,complete_download=True,actual_DB_R_listener_calls=0,old_targets=0,Golden_or_actual2252_target_reruns=0),sort_keys=True))
   finally:
    for c in clients:await c.aclose()
    for app in apps:app.state.complete_imports.close();await app.state.authority.close()
if __name__=='__main__':unittest.main()
