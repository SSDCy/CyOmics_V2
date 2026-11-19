"""One finite three-group combination; real HTTP/files/validator/supervision.

Original Owner repository methods and upstream DTO validation are explicit
doubles; no DB/network/native R. The fixed launcher process is an in-memory
double which invokes execute with a synthetic native Output. No old suite runs.
"""
from pathlib import Path
from unittest.mock import patch
from contextlib import ExitStack
from types import SimpleNamespace
from uuid import uuid4
import asyncio,base64,copy,hashlib,json,os,socket,struct,subprocess,sys,tempfile,time,unittest,zlib

BACKEND=Path(__file__).resolve().parents[1];sys.path.insert(0,str(BACKEND))
import httpx
from cyomics_projects import volcano_on_demand as v,volcano_runner as r,classification_on_demand as shared
from cyomics_projects import volcano_owner_read as plots,de_classification_repository as saved
from cyomics_projects.differential_worker_once import ResultReader
from cyomics_projects.app import create_app
from cyomics_projects.settings import EXPECTED_ISSUER,Fault,Budget

A='11111111-1111-4111-8111-111111111111';B='22222222-2222-4222-8222-222222222222';P='33333333-3333-4333-8333-333333333333'
S='44444444-4444-4444-8444-444444444444';CID='55555555-5555-4555-8555-555555555555'
PARAM={'schema_version':'de_classification_v0.1','input_scale':'log2','adjusted_p_max':'0.05','absolute_log2fc_min':'1'}
def tsv(header,rows):return ('\t'.join(header)+'\n'+''.join('\t'.join(row)+'\n' for row in rows)).encode()
def kv(values,keys):return tsv(('key','value'),[(k,str(values[k])) for k in keys])
ROW=('1','synthetic-protein',CID,'testable','ok','2','4','finite','8','3','4','5','0.001','0.01','3','3','3','3','Up','TRUE')
PARAMS=kv(PARAM,r.contract.PARAMETER_KEYS)
AUDIT={k:'none' for k in r.contract.SOURCE_AUDIT_KEYS}
AUDIT.update(schema_version='de_classification_v0.1',classification_status='CLASSIFIED',DE_members_evaluated='TRUE',
    source_contract='limma_core_v0.1',source_output_status='COMPUTED_CANDIDATE',source_warning_count='0',source_contrast_id=CID,
    source_numerator='g1',source_denominator='g2',contrast_direction='numerator_minus_denominator',threshold_scale='log2FC',
    adjusted_p_max='0.05',absolute_log2fc_min='1',boundary_rule=r.contract.BOUNDARY,input_rows='1',source_testable_rows='1',
    source_not_testable_rows='0',Up='1',Down='0',NS='0',not_testable='0',not_evaluated='0',selected_DE_rows='1',
    source_bh_method='BH',source_bh_m='1',source_bh_n='1',source_bh_scope='all_testable_rows',statistical_recomputation='none',
    row_identity='source_row_ordinal_no_ProteinID_deduplication',row_order='original')
INPUTS={'classification.tsv':tsv(r.contract.CLASSIFICATION_HEADER,[ROW]),'classification-parameters.tsv':PARAMS,
    'classification-audit.tsv':kv(AUDIT,r.contract.SOURCE_AUDIT_KEYS)}
def synthetic_outputs():
    # Independent one-row expected display (x2, q.01 -> y2), not a statistical fit.
    values={'contract':r.contract.CONTRACT,'plot_status':'PLOTTED','input_classification_sha256':v.sha(INPUTS['classification.tsv']),
        'input_parameters_sha256':v.sha(PARAMS),'input_audit_sha256':v.sha(INPUTS['classification-audit.tsv']),
        'source_classification_status':'CLASSIFIED','source_contrast_id':CID,'source_numerator':'g1','source_denominator':'g2',
        'R_version':'R version 4.6.1','ggplot2_version':'4.0.3','digest_version':'0.6.39','x_source':'log2FC','axis':'adj.P.Val',
        'y_transform':'-log10(pmax(adj.P.Val,1e-300))','adjusted_p_max':'0.05','absolute_log2fc_min':'1','boundary_rule':r.contract.BOUNDARY,
        'threshold_y_floored':'FALSE','q_display_floor':'1e-300','q_zero_floor_rows':'0','q_small_floor_rows':'0','input_rows':'1',
        'plotted_rows':'1','excluded_rows':'0','statistical_recomputation':'none','row_identity':AUDIT['row_identity'],'row_order':'original',
        'png_width_px':'2400','png_height_px':'1800','png_dpi':'300','svg_width_in':'8','svg_height_in':'6',
        'image_download_format':'svg','internal_preview_format':'png','rendering_package':'ggplot2',
        'threshold_x_negative':'-1','threshold_x_positive':'1','threshold_y_true':'1.3010299956639813',
        'threshold_y_display':'1.3010299956639813','y_axis_upper':'2.1'}
    for prefix in ('input','plotted','excluded'):
        for label in r.contract.CLASSES:values[prefix+'_'+label]='1' if prefix!='excluded' and label=='Up' else '0'
    for reason in r.contract.REASONS:values['reason_'+reason]='1' if reason=='plotted' else '0'
    def chunk(kind,body):return struct.pack('>I',len(body))+kind+body+struct.pack('>I',zlib.crc32(kind+body)&0xffffffff)
    png=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',2400,1800,8,6,0,0,0))+chunk(b'pHYs',struct.pack('>IIB',11811,11811,1))+chunk(b'IDAT',zlib.compress((b'\0'+bytes(2400*4))*1800))+chunk(b'IEND',b'')
    return {'R-session.txt':b'R_VERSION\tR version 4.6.1\nGGPLOT2_VERSION\t4.0.3\nDIGEST_VERSION\t0.6.39\nR version 4.6.1 ggplot2_4.0.3 digest_0.6.39\n',
        'volcano-audit.tsv':kv(values,r.contract.AUDIT_KEYS),'volcano-points.tsv':tsv(r.contract.POINTS_HEADER,[ROW+('2','2','TRUE','plotted')]),
        'volcano.png':png,'volcano.svg':b'<svg xmlns="http://www.w3.org/2000/svg" width="576pt" height="432pt" viewBox="0 0 576 432"><circle cx="1" cy="1" r="1"/></svg>'}
def token(actor=A,role='authenticated'):
    enc=lambda x:base64.urlsafe_b64encode(json.dumps(x).encode()).decode().rstrip('=');now=int(time.time())
    return '.'.join([enc({'alg':'HS256'}),enc({'sub':actor,'role':role,'iss':EXPECTED_ISSUER,'aud':'authenticated','iat':now-5,'exp':now+600,'is_anonymous':False}),enc('synthetic')])
class Writer:
    def __init__(self,proc,child):self.proc=proc;self.child=child;self.data=b''
    def write(self,raw):self.data+=raw
    async def drain(self):await self.child(self.proc,self.data)
    def close(self):pass
    async def wait_closed(self):pass
class Transport:
    def __init__(self,bad=False):self._finished=not bad;self.closed=False
    def close(self):self.closed=True
    def is_closing(self):return self.closed
    def get_pipe_transport(self,fd):return None
class Process:
    def __init__(self,child,bad=False):
        self.returncode=None;self.stdout=asyncio.StreamReader();self.stderr=asyncio.StreamReader();self._transport=Transport(bad)
        self.event=asyncio.Event();self.stdin=Writer(self,child)
    async def wait(self):await self.event.wait();return self.returncode

class FiniteVolcanoCombination(unittest.IsolatedAsyncioTestCase):
    async def test_one_directed_combination(self):
        apps=[];clients=[];spawned=[];native=[];metas={};bad_sources=set();invalid_contracts=set();half=set();bad_cleanup=set()
        outputs=synthetic_outputs();validated=r.contract.validate_inputs(*(INPUTS[n] for n in r.INPUT_NAMES));r.contract.validate_outputs(validated,outputs)
        parent={'created_by':A,'project_id':P,'result_id':S,'manifest_sha256':'b'*64}
        def version():
            d=str(uuid4());metas[d]={'created_by':A,'project_id':P,'derived_version_id':d,'source_result_id':S,'source_manifest_sha256':parent['manifest_sha256'],
                'source_binding':{'source_metadata_sha256':saved.digest(parent)},'source_binding_sha256':'c'*64,'parameters':copy.deepcopy(PARAM),
                'parameters_sha256':v.sha(PARAMS),'status':'CLASSIFIED','DE_members_evaluated':True,'manifest_sha256':'d'*64,'core':saved.core(),
                'artifacts':[{'name':n,'bytes':len(raw),'sha256':v.sha(raw)} for n,raw in INPUTS.items()]};return d
        async def read_meta(repo,actor,project,d,budget):
            if actor!=A or project!=P or d not in metas:raise Fault(404,'de_classification_version_not_found')
            out=copy.deepcopy(metas[d])
            if d in invalid_contracts:
                out['artifacts']=[{'name':n,'bytes':len(b'invalid\n') if n=='classification.tsv' else len(raw),'sha256':v.sha(b'invalid\n') if n=='classification.tsv' else v.sha(raw)} for n,raw in INPUTS.items()]
            return out
        async def read_art(repo,actor,project,d,name,budget):
            await read_meta(repo,actor,project,d,budget)
            return b'corrupted\n' if d in bad_sources else b'invalid\n' if d in invalid_contracts and name=='classification.tsv' else INPUTS[name]
        async def read_parent(repo,actor,project,budget,*,result_id=None,**kwargs):
            if (actor,project,result_id)!=(A,P,S):raise Fault(404,'not_found')
            return copy.deepcopy(parent)
        async def auth(request):
            actor=json.loads(base64.urlsafe_b64decode(request.headers['authorization'].split('.')[1]+'=='))['sub']
            return httpx.Response(200,json={'id':actor,'role':'authenticated','aud':'authenticated','is_anonymous':False,'email':'synthetic@example.invalid','email_confirmed_at':'2026-01-01T00:00:00Z'})
        def run_native(executor,inputs=None,*,cancelled=lambda:False):
            cfg=executor._config;self.assertEqual(inputs,INPUTS);self.assertFalse(cancelled());native.append(cfg.render_id)
            argv=executor._invocation(SimpleNamespace(NATIVE_EXECUTABLE=Path('/usr/lib/R/bin/exec/R')),cfg.staging_root/(cfg.render_id+'.'+cfg.attempt_id+'.partial'))
            self.assertEqual(argv[0:3],['/usr/lib/R/bin/exec/R','--vanilla','--slave']);self.assertEqual(argv[4],'--args');self.assertEqual([Path(x).name for x in argv[5:]],list(r.INPUT_NAMES)+['output'])
            return r.Output(copy.deepcopy(outputs),{'all_five_outputs_verified':True,'binding_sha256':cfg.binding_sha256,'profile_sha256':v.sha(cfg.profile_json)})
        original_publish=v.publish
        def publication(cfg,binding,bodies,rid):
            if binding.context['derived_version_id'] in half:
                q=cfg.publish_root/('.render-'+rid+'.partial');q.mkdir(mode=0o700);(q/'volcano.svg').write_bytes(bodies['volcano.svg']);raise Fault(503,'r_volcano_unavailable')
            return original_publish(cfg,binding,bodies,rid)
        async def spawn(controller):
            argv=controller._argv();self.assertEqual(argv,(shared.PYTHON,'-I','-B',str(BACKEND.parent/'controls/volcano_service_startup.py'),'--once'));spawned.append(argv)
            async def child(proc,raw):
                n=v.parse_notification(raw);phase='unknown';code=1
                try:
                    phase=await v.execute(controller.render_config,n,controller.reader,native_pins=(),verify=lambda:None)
                    code=0
                except Exception:pass
                value={'schemaVersion':1,'render_id':n['job_id'],'request_id':n['request_id'],'binding_sha256':n['binding_sha256'],
                    'state':phase,'disposition':'confirmed' if code==0 else 'unknown','teardown':'confirmed' if code==0 else 'unknown',
                    'loop_closed':True,'pending_tasks_seen':False,'async_resources_remaining':False,'interrupted':False,'exit_code':code}
                proc.stdout.feed_data(shared.canonical(value)+b'\n');proc.stdout.feed_eof();proc.stderr.feed_eof();proc.returncode=code;proc.event.set()
            return Process(child,controller.binding.context['derived_version_id'] in bad_cleanup)
        def intent(controller,value,*,job_key):
            # Managed test mount maps the root ancestor owner to65534. The actual-A
            # ancestor/launch identity check is doubled; exclusive intent bytes/fsync remain real.
            self.assertEqual(job_key,'job_id');self.assertEqual(set(value),v.IPC_KEYS)
            fd=os.open(controller.config.control,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            try:
                f=os.open(value[job_key]+'.launch.once',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
                try:raw=shared.canonical(value)+b'\n';self.assertEqual(os.write(f,raw),len(raw));os.fsync(f)
                finally:os.close(f)
                os.fsync(fd)
            finally:os.close(fd)
        with tempfile.TemporaryDirectory(prefix='volcano-finite-',dir=Path(__file__).resolve().parents[3]/'evidence') as temporary,ExitStack() as stack:
            root=Path(temporary);roots=[]
            for name in ['control','staging','published']:q=root/name;q.mkdir(mode=0o700);roots.append(q)
            control,staging,published=roots
            profile={'contract':'r_volcano_profile_v0.1','library_root':'/usr/local/lib/R/site-library','library_metadata':[dict(path=p,bytes=n,sha256=s) for p,n,s in r.KNOWN_METADATA],
                'font':r.KNOWN_FONT,'versions':{'R':'4.6.1','ggplot2':'4.0.3','digest':'0.6.39'},'adapter':{'bytes':(BACKEND/'cyomics_projects/volcano_runner.py').stat().st_size,'sha256':v.sha((BACKEND/'cyomics_projects/volcano_runner.py').read_bytes())}}
            cfg=v.Config(shared.Config(True,BACKEND.parent/'controls/volcano_service_startup.py',BACKEND,control,'a'*64,'b'*64,lambda:None),staging,published,
                v.canonical(profile),v.canonical({'project_id':P,'source_result_id':S,'parameters':PARAM}))
            def client():
                app=create_app(auth_transport=httpx.MockTransport(auth),pool=object(),anon_key_loader=lambda:token(role='anon'),classification_results_enabled=True,
                    volcano_read_config=plots.Config(True,published,1000),volcano_dispatch_config=cfg);apps.append(app)
                c=httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:57430');clients.append(c);return c,app.state.volcano_dispatch
            headers={'authorization':'Bearer '+token(),'origin':'http://127.0.0.1:4206','sec-fetch-site':'same-origin','x-cyomics-request':'1'}
            path=lambda d:'/api/v1/projects/'+P+'/derived-classifications/'+d+'/volcano/render'
            async def settled(k):
                task=k._monitor
                if task is not None:await task
            no_network=stack.enter_context(patch.object(socket.socket,'connect',side_effect=AssertionError('network forbidden')))
            no_native=stack.enter_context(patch.object(subprocess,'Popen',side_effect=AssertionError('native process forbidden')))
            no_async=stack.enter_context(patch('asyncio.create_subprocess_exec',side_effect=AssertionError('native process forbidden')))
            stack.enter_context(patch.object(saved.ClassificationRepository,'read_version',read_meta));stack.enter_context(patch.object(saved.ClassificationRepository,'read_artifact',read_art));stack.enter_context(patch.object(ResultReader,'read',read_parent))
            stack.enter_context(patch.object(saved,'validate_dto',side_effect=lambda x:x)) # Upstream original DTO/source persistence proof is an explicit double.
            stack.enter_context(patch.object(shared.Controller,'_sources',lambda self:None));stack.enter_context(patch.object(shared.Controller,'_spawn',spawn));stack.enter_context(patch.object(shared.Controller,'_pin',side_effect=lambda proc:os.open('/dev/null',os.O_RDONLY)))
            stack.enter_context(patch.object(shared.Controller,'_intent',intent))
            stack.enter_context(patch.object(r.VolcanoRunner,'run',run_native));stack.enter_context(patch.object(v,'publish',publication))
            groups=[]
            try:
                c,k=client();d=version();request=str(uuid4());response=await c.post(path(d),json={'request_id':request},headers=headers)
                self.assertEqual(response.status_code,201,(response.text,k.snapshot(),len(spawned)));rid=response.json()['render_id'];self.assertEqual(response.json()['state'],'queued');await settled(k)
                response=await c.get(path(d)+'?request_id='+request,headers=headers);self.assertEqual(response.status_code,200,response.text);self.assertEqual(response.json()['state'],'ready',response.text)
                self.assertEqual(response.json()['descriptor']['derived_version_id'],d);self.assertEqual(set(q.name for q in (published/d).iterdir()),set(plots.NAMES)|{'metadata.json'})
                response=await c.get(path(d).removesuffix('/render'),headers=headers);self.assertEqual(response.status_code,200,response.text)
                groups.append('explicit201_original_fixed_launcher_execute_real_five_output_atomic_cache_original_reader_ready')
                self.assertEqual((len(spawned),len(native)),(1,1));response=await c.post(path(d),json={'request_id':request},headers=headers);self.assertEqual(response.status_code,200,response.text)
                alias_request=str(uuid4());response=await c.post(path(d),json={'request_id':alias_request},headers=headers);self.assertEqual(response.status_code,200,response.text);self.assertEqual(response.json()['render_id'],rid)
                c2,k2=client();response=await c2.post(path(d),json={'request_id':str(uuid4())},headers=headers);self.assertEqual(response.status_code,200,response.text);self.assertEqual(response.json()['state'],'ready');self.assertEqual((len(spawned),len(native)),(1,1))
                bad_d=version();half.add(bad_d);rq=str(uuid4());response=await c2.post(path(bad_d),json={'request_id':rq},headers=headers);self.assertEqual(response.status_code,201,response.text);await settled(k2)
                self.assertFalse((published/bad_d).exists());response=await c2.get(path(bad_d)+'?request_id='+rq,headers=headers);self.assertEqual(response.status_code,200);self.assertEqual(response.json()['state'],'unknown');self.assertTrue(k2._capacity.unknown)
                n=len(spawned);c3,k3=client();response=await c3.post(path(bad_d),json={'request_id':str(uuid4())},headers=headers);self.assertEqual(response.status_code,504,response.text);self.assertEqual(len(spawned),n)
                c4,k4=client();response=await c4.post(path(version()),json={'request_id':str(uuid4())},headers=headers);self.assertEqual(response.status_code,503);self.assertTrue(k4._capacity.unknown);self.assertEqual(len(spawned),n)
                groups.append('same_binding_alias_and_restart_no_second_spawn_half_publication_unknown_never_ready_and_restart_capacity_fenced')
                c5,k5=client();d5=version();n=len(spawned);foreign=dict(headers,authorization='Bearer '+token(B));response=await c5.post(path(d5),json={'request_id':str(uuid4())},headers=foreign);self.assertEqual(response.status_code,404)
                bad_sources.add(d5);response=await c5.post(path(d5),json={'request_id':str(uuid4())},headers=headers);self.assertEqual(response.status_code,503,response.text)
                d6=version();invalid_contracts.add(d6);response=await c5.post(path(d6),json={'request_id':str(uuid4())},headers=headers);self.assertEqual(response.status_code,503,response.text)
                response=await c5.post(path(d5),json={'request_id':str(uuid4()),'script':'evil'},headers=headers);self.assertEqual(response.status_code,400)
                response=await c5.get(path(d5)+'?request_id='+str(uuid4())+'&x=1',headers=headers);self.assertEqual(response.status_code,400)
                response=await c5.post(path(d5).replace('/volcano/','/%76olcano/'),json={'request_id':str(uuid4())},headers=headers);self.assertEqual(response.status_code,404)
                changed=copy.deepcopy(profile);changed['library_root']='/tmp/arbitrary';self.assertRaises(r.shared.Log2RunFailure,r.profile,v.canonical(changed));self.assertEqual(len(spawned),n)
                groups.append('original_actor_context_source_byte_pins_and_fixed_path_body_R_contract_profile_refuse_before_consumption')
                self.assertEqual((no_network.call_count,no_native.call_count,no_async.call_count),(0,0,0))
                print(json.dumps({'finite_combination':'PASS','scenario_groups':groups,'synthetic_plot_R_outputs':True,'live_R_DB_Auth_listener_calls':0,'old_suites':0,
                    'original_exercised':['Authority via MockTransport','HTTP/app integration','durable claim/alias/execution latch','fixed launcher argv and shared supervision/cleanup','original input/output validators','original FileProvider/OwnerReader','original atomic rename_noreplace'],
                    'doubles':['upstream Owner repository methods and DTO persistence proof','Root physical source/ancestor/launch identity check','launcher process and actual R Output']}))
            finally:
                for c in clients:await c.aclose()
                for app in apps:app.state.complete_imports.close();await app.state.authority.close()

if __name__=='__main__':unittest.main()
