"""One finite controller target for volcano and heatmap. Synthetic Owner binding,
launcher child and runner output only; real Store/claims/aliases/receipt parsing,
shared supervisor teardown and failure records. Never native R/DB/network.
"""
from pathlib import Path
import asyncio,copy,json,os,socket,subprocess,sys,tempfile,unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4
BACKEND=Path(__file__).resolve().parents[1];sys.path.insert(0,str(BACKEND))
from cyomics_projects import volcano_on_demand as v,heatmap_on_demand as h,classification_on_demand as shared
from cyomics_projects.settings import Budget,Fault
A='11111111-1111-4111-8111-111111111111';P='22222222-2222-4222-8222-222222222222';S='33333333-3333-4333-8333-333333333333'
PARAM={'schema_version':'de_classification_v0.1','input_scale':'log2','adjusted_p_max':'0.05','absolute_log2fc_min':'1'}
class Writer:
 def __init__(self,proc,child):self.proc=proc;self.child=child;self.data=b''
 def write(self,raw):self.data+=raw
 async def drain(self):await self.child(self.proc,self.data)
 def close(self):pass
 async def wait_closed(self):pass
class Transport:
 _finished=True
 def __init__(self):self.closed=False
 def close(self):self.closed=True
 def is_closing(self):return self.closed
 def get_pipe_transport(self,fd):return None
class Process:
 def __init__(self,child):
  self.returncode=None;self.stdout=asyncio.StreamReader();self.stderr=asyncio.StreamReader();self._transport=Transport();self.event=asyncio.Event();self.stdin=Writer(self,child)
 async def wait(self):await self.event.wait();return self.returncode
class ManualRetryTarget(unittest.IsolatedAsyncioTestCase):
 async def test_finite_manual_retry_both_plots(self):
  results=[]
  with tempfile.TemporaryDirectory(prefix='manual-plot-retry-') as temporary,ExitStack() as stack:
   root=Path(temporary);spawns=[];runs=[];fail_next=set();bindings={};module_for={};controllers=[]
   stack.enter_context(patch.object(socket.socket,'connect',side_effect=AssertionError('network forbidden')))
   no_process=stack.enter_context(patch.object(subprocess,'Popen',side_effect=AssertionError('native forbidden')))
   no_async_process=stack.enter_context(patch('asyncio.create_subprocess_exec',side_effect=AssertionError('native forbidden')))
   def binding(d):
    return v.Binding({'context':{'actor_id':A,'project_id':P,'derived_version_id':d,'source_result_id':S},'inputs':[]}, {},object())
   async def fake_bind(reader,actor,project,d,budget,cfg):
    self.assertEqual((actor,project),(A,P));await asyncio.sleep(0);return bindings[d]
   async def controller_bind(k,actor,project,d,budget):return await fake_bind(None,actor,project,d,budget,k.render_config)
   def descriptor(k,b):
    self.assertTrue((k.render_config.publish_root/b.context['derived_version_id']).exists());return {'test_only_descriptor':True}
   def publication(cfg,b,outputs,rid):
    # Explicit output/publication double; this target proves controller retry semantics, not scientific rendering.
    (cfg.publish_root/b.context['derived_version_id']).mkdir(mode=0o700)
   def run_native(executor,inputs=None,*,cancelled=lambda:False):
    cfg=executor._config;runs.append(cfg.render_id);m=module_for[type(executor)]
    if str(cfg.staging_root) in fail_next:
     fail_next.remove(str(cfg.staging_root))
     facts={'schemaVersion':1,'job_id':cfg.render_id,'attempt_id':cfg.attempt_id,'configuration_sha256':v.sha(v.canonical({'binding_sha256':cfg.binding_sha256,'profile_sha256':v.sha(cfg.profile_json)})),
      'popen_attempted':False,'launch_state':'not_started','process_identity':None,'exit_code':None,'child_reaped':True,'observed_tasks_stopped':True,'owned_fds_closed':True,'timer_disabled':True,'signal_handler_restored':True,'process_evidence_sha256':None,'cleanup_known':True}
     raise m.runner.shared.Log2RunFailure('unsafe_log2_ancestry',m.runner.shared.FailureFacts(v.canonical(facts)))
    key='all_five_outputs_verified' if m is v else 'all_eight_outputs_verified'
    return m.runner.Output({}, {key:True,'binding_sha256':cfg.binding_sha256,'profile_sha256':v.sha(cfg.profile_json)})
   def intent(k,value,*,job_key):
    self.assertEqual(job_key,'job_id');q=k.config.control/(value[job_key]+'.launch.once')
    fd=os.open(q,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    try:os.write(fd,shared.canonical(value)+b'\n');os.fsync(fd)
    finally:os.close(fd)
   async def spawn(k):
    spawns.append(k.render_id);m=v if type(k) is v.Controller else h
    async def child(proc,raw):
     n=m.parse_notification(raw);phase='unknown';code=1
     try:phase=await m.execute(k.render_config,n,k.reader,native_pins=(),verify=lambda:None);code=0
     except Exception:pass
     value={'schemaVersion':1,'render_id':n['job_id'],'request_id':n['request_id'],'binding_sha256':n['binding_sha256'],'state':phase,'disposition':'confirmed' if code==0 else 'unknown','teardown':'confirmed' if code==0 else 'unknown','loop_closed':True,'pending_tasks_seen':False,'async_resources_remaining':False,'interrupted':False,'exit_code':code}
     proc.stdout.feed_data(shared.canonical(value)+b'\n');proc.stdout.feed_eof();proc.stderr.feed_eof();proc.returncode=code;proc.event.set()
    return Process(child)
   for m in (v,h):
    stack.enter_context(patch.object(m,'bind',fake_bind));stack.enter_context(patch.object(m,'publish',publication))
    typ=m.runner.VolcanoRunner if m is v else m.runner.HeatmapRunner;module_for[typ]=m;stack.enter_context(patch.object(typ,'run',run_native))
    stack.enter_context(patch.object(m.Controller,'_bind',controller_bind));stack.enter_context(patch.object(m.Controller,'_reader_ok',staticmethod(lambda reader,cfg:True)));stack.enter_context(patch.object(m.Controller,'_descriptor',descriptor))
   stack.enter_context(patch.object(shared.Controller,'_sources',lambda k:None));stack.enter_context(patch.object(shared.Controller,'_intent',intent));stack.enter_context(patch.object(shared.Controller,'_spawn',spawn));stack.enter_context(patch.object(shared.Controller,'_pin',lambda k,proc:os.open('/dev/null',os.O_RDONLY)))
   def new(m,label):
    folder=root/label;folder.mkdir(mode=0o700)
    for name in ['control','staging','published']:(folder/name).mkdir(mode=0o700)
    r=m.runner;kind='volcano' if m is v else 'heatmap'
    profile={'contract':'r_'+kind+'_profile_v0.1','library_root':'/usr/local/lib/R/site-library','library_metadata':[dict(path=p,bytes=n,sha256=s) for p,n,s in r.KNOWN_METADATA],'font':r.KNOWN_FONT,'versions':{'R':'4.6.1','digest':'0.6.39',**({'ggplot2':'4.0.3'} if m is v else {'pheatmap':'1.0.13'})},'adapter':{'bytes':1,'sha256':'a'*64}}
    cfg=m.Config(shared.Config(True,BACKEND.parent/'controls'/(''+kind+'_service_startup.py'),BACKEND,folder/'control','a'*64,'b'*64,lambda:None),folder/'staging',folder/'published',v.canonical(profile),v.canonical({'project_id':P,'source_result_id':S,'parameters':PARAM}))
    k=m.Controller(cfg,object());controllers.append(k);d=str(uuid4());bindings[d]=binding(d);return k,d,bindings[d]
   def seed(k,b,phase='failed'):
    rid=str(uuid4());rq=str(uuid4());store=k.store;store.write(v.claim_name(b),{'render_id':rid,'binding_sha256':b.digest,'identity':b.identity});store.write(v.alias_name(A,P,b.context['derived_version_id'],rq),v.alias(b,rq,rid));store.state(rid,b.digest,phase)
    # Historical once markers have no new native/cleanup receipt; never reset them.
    store.write('execute-'+rid+'.once',{'render_id':rid,'binding_sha256':b.digest});store.write(rid+'.launch.once',{'legacy_test_intent':True})
    return rid,rq
   async def get(k,d,rq):return await k.status(A,P,d,rq,Budget())
   async def post(k,d,rq):return await k.submit(A,P,d,{'request_id':rq},Budget())
   async def settle(k):
    if k._monitor is not None:await k._monitor
   for m in (v,h):
    kind='volcano' if m is v else 'heatmap';k,d,b=new(m,kind+'-legacy');oldrid,oldrq=seed(k,b);oldfiles={q.name:q.read_bytes() for q in k.config.control.iterdir()};n=len(spawns)
    self.assertEqual((await get(k,d,oldrq))['state'],'failed');self.assertEqual((await post(k,d,oldrq))[0]['render_id'],oldrid);self.assertEqual(len(spawns),n)
    rq1,rq2=str(uuid4()),str(uuid4());a,z=await asyncio.gather(post(k,d,rq1),post(k,d,rq2));self.assertEqual(sum(int(x[1]) for x in [a,z]),1);newrid=a[0]['render_id'];self.assertEqual(newrid,z[0]['render_id']);self.assertNotEqual(newrid,oldrid);await settle(k)
    self.assertEqual(len(spawns),n+1);self.assertEqual((await get(k,d,rq1))['state'],'ready');old=await get(k,d,oldrq);self.assertEqual((old['state'],old['render_id']),('failed',oldrid));self.assertEqual((await post(k,d,rq1))[0]['render_id'],newrid)
    self.assertEqual((await post(k,d,str(uuid4())))[0]['state'],'ready');self.assertEqual(len(spawns),n+1)
    for name,raw in oldfiles.items():self.assertEqual((k.config.control/name).read_bytes(),raw)
    self.assertEqual(len(list(k.config.control.glob('retry-after-*.json'))),1);self.assertEqual(len(list(k.config.control.glob('execute-*.once'))),2)
    # New failure diagnostic path, unchanged confirmed failed criterion, explicit second request succeeds.
    f,fd,fb=new(m,kind+'-future-failure');fail_next.add(str(f.render_config.staging_root));first=str(uuid4());dto,started=await post(f,fd,first);self.assertTrue(started);frid=dto['render_id'];await settle(f)
    self.assertEqual((await get(f,fd,first))['state'],'failed');failure=f.store.read('failure-'+frid+'.json');self.assertEqual(failure['error_code'],'unsafe_log2_ancestry');self.assertTrue(failure['failure_facts']['cleanup_known']);self.assertEqual(failure['binding_sha256'],fb.digest);self.assertEqual(os.stat(f.config.control/('failure-'+frid+'.json')).st_mode&0o777,0o600)
    again=str(uuid4());nextdto,started=await post(f,fd,again);self.assertTrue(started);await settle(f);self.assertEqual((await get(f,fd,again))['state'],'ready');self.assertEqual((await get(f,fd,first))['render_id'],frid);self.assertNotEqual(nextdto['render_id'],frid)
    # Non-failed phases never create a new render, even if a new request UUID arrives.
    for phase in ['unknown','ready','queued','running']:
     x,xd,xb=new(m,kind+'-'+phase);xr,xq=seed(x,xb,phase)
     if phase=='ready':(x.render_config.publish_root/xd).mkdir(mode=0o700)
     before=len(spawns)
     try:value,started=await post(x,xd,str(uuid4()));self.assertFalse(started);self.assertEqual(value['render_id'],xr)
     except Fault:pass
     self.assertEqual(len(spawns),before);self.assertFalse(list(x.config.control.glob('retry-after-*.json')))
    for reason in ['published','active','capacity']:
     x,xd,xb=new(m,kind+'-blocked-'+reason);seed(x,xb)
     if reason=='published':(x.render_config.publish_root/xd).mkdir(mode=0o700)
     if reason=='active':x.store.write('active-render.json',{'render_id':str(uuid4()),'binding_sha256':'f'*64})
     if reason=='capacity':self.assertTrue(x._capacity.take(object(),('synthetic',)))
     before=len(spawns)
     with self.assertRaises(Fault):await post(x,xd,str(uuid4()))
     self.assertEqual(len(spawns),before);self.assertFalse(list(x.config.control.glob('retry-after-*.json')))
    results.append({'kind':kind,'legacy_failed_explicit_retry_ready':True,'legacy_GET_and_once_bytes_preserved':True,'concurrent_two_POSTs_one_successor_and_launch':True,'new_failure_private_code_facts_and_manual_retry':True,'unknown_ready_queued_running_no_rerun':True,'published_active_capacity_blocked':True})
   self.assertEqual((no_process.call_count,no_async_process.call_count),(0,0));self.assertEqual(len(spawns),6);self.assertEqual(len(runs),6)
   for k in controllers:await k.close()
   print(json.dumps({'target':'PASS_ONE_FINITE_MANUAL_RETRY_TARGET','results':results,'synthetic_launcher_children':len(spawns),'synthetic_runner_calls':len(runs),'actual_R_DB_Auth_network_child_process_calls':0,'old_suites_run':0,'explicit_doubles':['Owner binding/DTO persistence','Root source and launcher identity/intent ancestry','launcher Popen/pidfd/pipe transport','native R output/publication/descriptor']},sort_keys=True))
if __name__=='__main__':unittest.main()
