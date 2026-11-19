"""One finite new export target. Reuses only historical pure fixture builders,
never their tests. Real Authority/HTTP/Owner-reader/file-provider/ZIP; repository
methods and design envelope are explicit doubles, never live DB/R/Auth/network.
"""
from pathlib import Path
from contextlib import ExitStack
from unittest.mock import patch
import base64,copy,hashlib,io,json,os,socket,subprocess,sys,tempfile,unittest,zipfile,traceback
BACKEND=Path(__file__).resolve().parents[1];sys.path.insert(0,str(BACKEND))
import httpx
import test_heatmap_owner_read as hf
import test_volcano_owner_read as vf
import test_volcano_generate_source as gf
from cyomics_projects import volcano_owner_read as v,heatmap_owner_read as h,plot_reproducibility as e,de_classification_repository as saved,differential_jobs as original
from cyomics_projects.app import create_app
from cyomics_projects import volcano_owner_http as vh,heatmap_owner_http as hh
from cyomics_projects.differential_worker_once import ResultReader
from cyomics_projects.settings import Fault
A=hf.A;OTHER=hf.OTHER;P=hf.P;V=hf.V;R=hf.R

def context(parent,meta):
 return {'project_id':P,'actor_id':A,'source_result_id':R,'source_metadata_sha256':saved.digest(parent),'source_manifest_sha256':parent['manifest_sha256'],'derived_version_id':V,'derived_metadata_sha256':saved.digest(meta),'derived_manifest_sha256':meta['manifest_sha256'],'source_binding_sha256':meta['source_binding_sha256'],'parameters_sha256':meta['parameters_sha256']}
def rebind(parent,meta):
 meta['source_binding']['source_metadata_sha256']=saved.digest(parent);meta['source_binding_sha256']=saved.digest(meta['source_binding']);saved.validate_dto(meta)
def registered(kind,parent,meta,inputs,outputs):
 ctx=context(parent,meta);d={'schemaVersion':1,'contract':'r_'+kind+'_saved_v0.1',**{('created_by' if k=='actor_id' else k):x for k,x in ctx.items()},'render_script':v.SCRIPT if kind=='volcano' else h.SCRIPT,'inputs':[{'name':n,**e.pin(inputs[n])} for n in (v.INPUTS if kind=='volcano' else h.INPUTS)],'artifacts':[{'name':n,**e.pin(outputs[n]),'media_type':(v.MEDIA if kind=='volcano' else h.MEDIA)[n]} for n in (v.NAMES if kind=='volcano' else h.NAMES)]}
 if kind=='volcano':d.update(render_source_derived_id=V,render_source_metadata_sha256=saved.digest(meta))
 return d
class ReproductionTarget(unittest.IsolatedAsyncioTestCase):
 async def test_one_finite_export_target(self):
  cases={};parent,meta,_,_,_=vf.fixture();parent.update(method=copy.deepcopy(original.METHOD),selection={'synthetic_saved_comparison':'fixed'},input_manifest={'matrix':{'bytes':1,'sha256':'a'*64}})
  inputs=copy.deepcopy(gf.INPUTS);outputs=gf.synthetic_outputs();meta['counts']={'Up':1,'Down':0,'NS':0,'not_testable':0,'not_evaluated':0,'selected_DE_rows':1};meta['member_set']['member_count']=1
  for item in meta['artifacts']:
   if item['name'] in inputs:item.update(e.pin(inputs[item['name']]))
  meta['manifest_sha256']=saved.digest(meta['artifacts']);rebind(parent,meta);reg=registered('volcano',parent,meta,inputs,outputs)
  cases['volcano']={'parent':parent,'meta':meta,'inputs':inputs,'outputs':outputs,'registration':reg}
  parent,meta,_,_,inputs,outputs,_=hf.fixture();parent['method']=copy.deepcopy(original.METHOD)
  # Full original source has2252 rows; display matrix retains only2 selected rows.
  inputs['matrix.tsv']+=b''.join((str(i)+'\tunselected-'+str(i)+'\tNA\tNA\tNA\tNA\tNA\tNA\n').encode() for i in range(21,2271));parent['input_manifest']['matrix']=e.pin(inputs['matrix.tsv']);rebind(parent,meta);ctx=context(parent,meta)
  provenance={'schemaVersion':1,'contract':'r_heatmap_provenance_v0.1',**{('created_by' if k=='actor_id' else k):x for k,x in ctx.items()},'inputs':[{'name':n,**e.pin(inputs[n])} for n in ['matrix.tsv','samples.tsv','selected-DE.tsv']]};inputs['provenance.json']=e.canonical(provenance)
  audit=dict(h.heatmap.table(outputs['heatmap-audit.tsv'],('key','value'),len(h.heatmap.AUDIT_KEYS)));audit.update(input_rows='2252',missing_cells=str(2250*6))
  for prefix,name in [('matrix','matrix.tsv'),('provenance','provenance.json')]:audit[prefix+'_bytes']=str(len(inputs[name]));audit[prefix+'_sha256']=e.pin(inputs[name])['sha256']
  outputs['heatmap-audit.tsv']=hf.tsv(('key','value'),[(k,audit[k]) for k in h.heatmap.AUDIT_KEYS]);verified=h.heatmap.validate_inputs(inputs['matrix.tsv'],inputs['selected-DE.tsv'],inputs['samples.tsv'],inputs['provenance.json']);h.heatmap.validate_outputs(verified,outputs);reg=registered('heatmap',parent,meta,inputs,outputs)
  cases['heatmap']={'parent':parent,'meta':meta,'inputs':inputs,'outputs':outputs,'registration':reg}
  state={'kind':'volcano'};cache_reads=[];repo_reads=[];apps=[];clients=[];proof=[];errors=[]
  def track_error(original):
   def observed(error,**kwargs):
    errors.append({'type':type(error).__name__,'code':getattr(error,'code',None),'frames':[(f.name,f.lineno) for f in traceback.extract_tb(error.__traceback__)]});return original(error,**kwargs)
   return observed
  async def auth(request):
   actor=json.loads(base64.urlsafe_b64decode(request.headers['authorization'].split('.')[1]+'=='))['sub']
   return httpx.Response(200,json={'id':actor,'role':'authenticated','aud':'authenticated','is_anonymous':False,'email':'synthetic@example.invalid','email_confirmed_at':'2026-01-01T00:00:00Z'})
  async def meta_read(repo,actor,project,version,budget):
   repo_reads.append(('meta',actor))
   if (actor,project,version)!=(A,P,V):raise Fault(404,'not_found')
   return copy.deepcopy(cases[state['kind']]['meta'])
  async def artifact_read(repo,actor,project,version,name,budget):
   await meta_read(repo,actor,project,version,budget);repo_reads.append(('input',name));return cases[state['kind']]['inputs'][name]
  async def parent_read(reader,actor,project,budget,**kwargs):
   if (actor,project,kwargs)!=(A,P,{'result_id':R}):raise Fault(404,'not_found')
   return copy.deepcopy(cases[state['kind']]['parent'])
  async def design_context(design_reader,actor,project,meta,parent,budget):
   self.assertEqual((actor,project),(A,P));case=cases['heatmap'];return context(parent,meta),case['registration']['inputs'],case['inputs']['samples.tsv'],case['inputs']['provenance.json']
  original_root=v.FileProvider._root
  def tracked_root(provider,stack,check):cache_reads.append(provider.config.root);return original_root(provider,stack,check)
  with tempfile.TemporaryDirectory(prefix='repro-target-',dir=BACKEND/'tests') as tmp,ExitStack() as stack:
   base=Path(tmp)
   stack.enter_context(patch.object(vh,'error_response',track_error(vh.error_response)));stack.enter_context(patch.object(hh,'error_response',track_error(hh.error_response)))
   stack.enter_context(patch.object(socket.socket,'connect',side_effect=AssertionError('network forbidden')));no_native=stack.enter_context(patch.object(subprocess,'Popen',side_effect=AssertionError('native forbidden')));no_async=stack.enter_context(patch('asyncio.create_subprocess_exec',side_effect=AssertionError('native forbidden')))
   stack.enter_context(patch.object(saved.ClassificationRepository,'read_version',meta_read));stack.enter_context(patch.object(saved.ClassificationRepository,'read_artifact',artifact_read));stack.enter_context(patch.object(ResultReader,'read',parent_read));stack.enter_context(patch.object(h,'bound_input_context',design_context));stack.enter_context(patch.object(v.FileProvider,'_root',tracked_root))
   roots={}
   def put(q,raw):
    if q.exists():q.chmod(0o600)
    q.write_bytes(raw);q.chmod(0o444)
   for kind,case in cases.items():
    root=base/kind;root.mkdir(mode=0o700);folder=root/V;folder.mkdir(mode=0o700);roots[kind]=root
    bodies={**case['outputs'],**(case['inputs'] if kind=='heatmap' else {}),'metadata.json':e.canonical(case['registration'])}
    for name,raw in bodies.items():put(folder/name,raw)
   app=create_app(auth_transport=httpx.MockTransport(auth),pool=object(),anon_key_loader=lambda:hf.token(role='anon'),classification_results_enabled=True,volcano_read_config=v.Config(True,roots['volcano'],1000),heatmap_read_config=h.Config(True,roots['heatmap'],1000));apps.append(app)
   client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:57430');clients.append(client)
   headers={'authorization':'Bearer '+hf.token(),'origin':'http://127.0.0.1:4206','sec-fetch-site':'same-origin','x-cyomics-request':'1'}
   contract=json.loads((BACKEND/'tests/fixtures/plot-reproducibility-contract-v0.1.json').read_text())
   try:
    for kind,case in cases.items():
     state['kind']=kind;path='/api/v1/projects/'+P+'/derived-classifications/'+V+'/'+kind+'/reproducibility';response=await client.get(path,headers=headers);self.assertEqual(response.status_code,200,(response.text[:200],errors));body=response.content
     self.assertEqual(response.headers['content-type'],'application/zip');self.assertEqual(response.headers['content-length'],str(len(body)));self.assertEqual(response.headers['x-cyomics-file-sha256'],hashlib.sha256(body).hexdigest());self.assertEqual(response.headers['content-disposition'],'attachment; filename="cyomics_'+kind+'_'+V+'_reproducibility.zip"')
     self.assertEqual([response.headers[x] for x in ['cache-control','x-content-type-options','referrer-policy']],['no-store','nosniff','no-referrer'])
     with zipfile.ZipFile(io.BytesIO(body)) as archive:
      self.assertEqual(archive.namelist(),contract['limits']['entry_names'][kind]);self.assertFalse(any(n.endswith(('.png','.pdf')) for n in archive.namelist()));members={n:archive.read(n) for n in archive.namelist()};manifest=json.loads(members['manifest.json'])
      self.assertEqual(set(manifest),set(contract['manifest']['top_level_keys']));self.assertEqual(manifest['context'],context(case['parent'],case['meta']));self.assertEqual(manifest['parameters'],case['meta']['parameters']);self.assertEqual(set(manifest['files']),set(members)-{'manifest.json'})
      for name,pin in manifest['files'].items():self.assertEqual(pin,e.pin(members[name]))
      for info in archive.infolist():self.assertEqual(info.date_time,(1980,1,1,0,0,0));self.assertEqual(info.compress_type,zipfile.ZIP_STORED)
      for script in ['volcano','heatmap']:self.assertEqual(members['scripts/'+script+'.R'],(BACKEND/'cyomics_projects'/e.SCRIPTS[script][0]).read_bytes())
      for name,raw in case['inputs'].items():self.assertEqual(members['inputs/'+name],raw)
      for name in e.OUTPUTS[kind]:self.assertEqual(members['outputs/'+name],case['outputs'][name])
      self.assertEqual(members['metadata.json'],e.canonical(case['registration']));self.assertEqual(manifest['method']['argv'],list(e.ARGV[kind]));self.assertIn(' '.join(e.ARGV[kind]).encode(),members['README.txt'])
      if kind=='heatmap':self.assertEqual(members['inputs/matrix.tsv'].count(b'\n')-1,2252);self.assertEqual(members['outputs/heatmap-matrix.tsv'].count(b'\n')-1,2);self.assertNotEqual(members['inputs/matrix.tsv'],members['outputs/heatmap-matrix.tsv'])
     second=await client.get(path,headers=headers);self.assertEqual(second.status_code,200);self.assertEqual(second.content,body)
     before=len(cache_reads);foreign=dict(headers,authorization='Bearer '+hf.token(OTHER));response=await client.get(path,headers=foreign);self.assertEqual(response.status_code,404);self.assertEqual(len(cache_reads),before)
     for method,suffix,content,status in [('POST','',b'',405),('GET','?x=1',b'',400),('GET','',b'bad',400)]:self.assertEqual((await client.request(method,path+suffix,content=content,headers=headers)).status_code,status)
     self.assertEqual((await client.get(path.replace('/'+kind+'/','/%'+format(ord(kind[0]),'02x')+kind[1:]+'/'),headers=headers)).status_code,404)
     folder=roots[kind]/V;backup=folder.with_name('not-ready');folder.rename(backup);self.assertEqual((await client.get(path,headers=headers)).status_code,404);backup.rename(folder)
     required='matrix.tsv' if kind=='heatmap' else kind+'.svg';missing=folder/required;hold=folder/('hold-'+required);missing.rename(hold);self.assertEqual((await client.get(path,headers=headers)).status_code,404);hold.rename(missing)
     svg=folder/(kind+'.svg');put(svg,b'corrupt');self.assertEqual((await client.get(path,headers=headers)).status_code,503);put(svg,case['outputs'][kind+'.svg'])
     # Exercise exact serialized ZIP overhead bound, not a scientific output limit.
     with patch.object(e,'MAX_ARCHIVE',len(body)-1):
      oversized=await client.get(path,headers=headers);self.assertEqual(oversized.status_code,413,oversized.text);self.assertEqual(oversized.json(),{'error':{'code':'r_'+kind+'_reproducibility_too_large'}})
     self.assertEqual(e.MAX_ARCHIVE,33554432)
     proof.append({'kind':kind,'entry_count':len(members),'all_hashes_exact_deterministic_zip':True,'SVG_only_original_scripts_and_inputs':True,'Owner_notready_missing_tamper_exact_GET_guards':True,'zip_overhead_limit_413':True})
    # Narrow middleware cap only: authorized route with an explicit export-result double.
    state['kind']='volcano';large=b'ZIP_RESPONSE_BOUNDARY_DOUBLE'+b'x'*(28*1024*1024)
    async def large_export(reader,actor,project,version,budget):
     self.assertEqual((actor,project,version),(A,P,V));return e.Export(large,hashlib.sha256(large).hexdigest(),'cyomics_volcano_'+V+'_reproducibility.zip')
    with patch.object(v.OwnerReader,'reproducibility',large_export):
     path='/api/v1/projects/'+P+'/derived-classifications/'+V+'/volcano/reproducibility';response=await client.get(path,headers=headers);self.assertEqual(response.status_code,200);self.assertEqual(len(response.content),len(large));self.assertGreater(len(large),26*1024*1024);self.assertLess(len(large),e.MAX_ARCHIVE)
    self.assertEqual((no_native.call_count,no_async.call_count),(0,0))
    print(json.dumps({'target':'PASS_ONE_FINITE_REPRO_EXPORT_TARGET','groups':proof,'narrow_32MiB_response_marker':True,'actual_R_DB_write_Auth_network_listener_or_child_process_calls':0,'old_test_methods_run':0,'explicit_doubles':['upstream Owner metadata/artifact/parent repository methods','heatmap design-context envelope','HTTP Auth via MockTransport','one28MiB middleware-only export result, not a real archive/science proof'],'original_exercised':['Authority and exact HTTP budget/header/expiry guards','Owner reader proof ordering','immutable FileProvider identity/byte checks','original pure plot input/output fixtures','fixed original script file pins','ZIP memory bounds/entry hashes/determinism']},sort_keys=True))
   finally:
    for client in clients:await client.aclose()
    for app in apps:app.state.complete_imports.close();await app.state.authority.close()
if __name__=='__main__':unittest.main()
