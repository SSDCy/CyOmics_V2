"""One finite saved-input heatmap combination. No real R/DB/Factory/signals.
Original validators/bound_samples/files/publisher/controller run; upstream
repository/DTO proofs, physical launch and native output are explicit doubles.
"""
from pathlib import Path
from contextlib import ExitStack,redirect_stdout
from unittest.mock import Mock,patch
from types import SimpleNamespace
from uuid import uuid4
import asyncio,base64,copy,io,json,math,os,socket,stat,struct,subprocess,sys,tempfile,time,unittest,zlib
BACKEND=Path(__file__).resolve().parents[1];sys.path.insert(0,str(BACKEND))
import httpx
from cyomics_projects import heatmap_on_demand as h,heatmap_runner as r,heatmap_owner_read as plots
from cyomics_projects import volcano_on_demand as vg,classification_on_demand as shared,de_classification_repository as saved,raw_qc_once_entry as raw
from cyomics_projects.differential_worker_once import ResultReader
from cyomics_projects.sample_repository import SampleDesignRepository
from cyomics_projects.app import create_app
from cyomics_projects.settings import EXPECTED_ISSUER,Fault

def uid(n):return f'{n:08d}-{n:04d}-4{n:03d}-8{n:03d}-{n:012d}'
A,B,P,S,JOB,DS,V,SD,DR,CID=[uid(n) for n in range(1,11)]
PARAM={'schema_version':'de_classification_v0.1','input_scale':'log2','adjusted_p_max':'0.05','absolute_log2fc_min':'1'}
def tsv(header,rows):return ('\t'.join(header)+'\n'+''.join('\t'.join(map(str,row))+'\n' for row in rows)).encode()
def kv(values,keys):return tsv(('key','value'),[(key,values[key]) for key in keys])
PARAMS=kv(PARAM,('schema_version','input_scale','adjusted_p_max','absolute_log2fc_min'))
ROW=('1','synthetic-protein',CID,'testable','ok','2','4','finite','8','3','4','5','0.001','0.01','3','3','3','3','Up','TRUE')
MATRIX=tsv(r.contract.MATRIX_HEADER,[('1','synthetic-protein','1','2','3','4','5','6'),('2','other','NA','NA','NA','NA','NA','NA')])
SELECTED=tsv(r.contract.SELECTED_HEADER,[ROW])
DESIGN_ROWS=[{'column_ordinal':i+2,'name_raw':'LFQ sample'+str(i),'sample_label_raw':'sample'+str(i),'group':'Treatment' if i<=3 else 'WT',
    'condition':'','control':'unknown','replicate':str((i-1)%3+1),'replicate_type':'biological','confirmed':True} for i in range(1,7)]
DESIGN=tsv(('sample_key','group_key','replicate_key','replicate_type','confirmed'),[(f's{i}','g1' if i<=3 else 'g2',f'r{i}','biological','true') for i in range(1,7)])
PRESET=tsv(('key','value'),[('schema_version','limma_core_v0.1'),('input_scale','log2'),('upstream_transform','positive_log2'),('upstream_zero_blank','NA'),('pseudocount','0'),('normalization','none'),('imputation','none'),('replicate_type','biological'),('design_confirmed','true'),('contrast_id',CID),('numerator','g1'),('denominator','g2')])
def outputs(inputs):
    value=r.contract.validate_inputs(*(inputs[n] for n in r.INPUT_NAMES));mean=3.5;sd=math.sqrt(3.5);zs=[(i-mean)/sd for i in range(1,7)];fmt=lambda n:format(n,'.17g')
    audit=dict(r.contract.FIXED,input_rows='2',selected_rows='1',samples='6',observed_cells='6',missing_cells='6',plotted_observed_cells='6',plotted_missing_cells='0',scaled_rows='1',zero_sd_rows='0',all_missing_rows='0',insufficient_observations_rows='0',color_limit=fmt(max(map(abs,zs))),R_version='R version 4.6.1',pheatmap_version='1.0.13')
    for prefix,body in [('matrix',value.matrix),('selected_DE',value.selected),('samples',value.samples),('provenance',value.provenance)]:audit[prefix+'_bytes']=str(len(body));audit[prefix+'_sha256']=h.sha(body)
    def chunk(kind,body):return struct.pack('>I',len(body))+kind+body+struct.pack('>I',zlib.crc32(kind+body)&0xffffffff)
    png=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',2400,1350,8,6,0,0,0))+chunk(b'pHYs',struct.pack('>IIB',11811,11811,1))+chunk(b'IDAT',zlib.compress((b'\0'+bytes(2400*4))*1350))+chunk(b'IEND',b'')
    result={'R-session.txt':b'R_VERSION\tR version 4.6.1\nPHEATMAP_VERSION\t1.0.13\nDIGEST_VERSION\t0.6.39\nR version 4.6.1 pheatmap_1.0.13 digest_0.6.39\n',
        'heatmap-audit.tsv':kv(audit,r.contract.AUDIT_KEYS),'heatmap-matrix.tsv':tsv(r.contract.MATRIX_HEADER,[('1','synthetic-protein','1','2','3','4','5','6')]),
        'heatmap-row-map.tsv':tsv(r.contract.ROW_MAP_HEADER,[('1','synthetic-protein','Up','6',fmt(mean),fmt(sd),'scaled')]),
        'heatmap-samples.tsv':value.samples,'heatmap-zscore.tsv':tsv(r.contract.MATRIX_HEADER,[('1','synthetic-protein',*map(fmt,zs))]),
        'heatmap.png':png,'heatmap.svg':b'<svg xmlns="http://www.w3.org/2000/svg" width="1152pt" height="648pt" viewBox="0 0 1152 648"><rect width="1" height="1"/></svg>'}
    r.contract.validate_outputs(value,result);return result
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
    _finished=True
    def __init__(self):self.closed=False
    def close(self):self.closed=True
    def is_closing(self):return self.closed
    def get_pipe_transport(self,fd):return None
class Process:
    def __init__(self,child):
        self.returncode=None;self.stdout=asyncio.StreamReader();self.stderr=asyncio.StreamReader();self._transport=Transport()
        self.event=asyncio.Event();self.stdin=Writer(self,child)
    async def wait(self):await self.event.wait();return self.returncode

class HeatmapFiniteCombination(unittest.IsolatedAsyncioTestCase):
    async def test_saved_heatmap_combination(self):
        apps=[];clients=[];metas={};spawned=[];natives=[];queries=[];bad_matrix=set();bad_design=set();bad_selected=set();partial=set()
        pin=lambda body:{'bytes':len(body),'sha256':h.sha(body)}
        parent={'created_by':A,'project_id':P,'result_id':S,'job_id':JOB,'manifest_sha256':'b'*64,
            'input_manifest':{'matrix':pin(MATRIX),'design':pin(DESIGN),'preset':pin(PRESET)},'selection':{'processed_dataset_version_id':V,'sample_design_version_id':SD,'sample_design_request_id':DR,
            'replicate_model':'independent_biological','contrast':{'schemaVersion':1,'dataset_version_id':DS,'quant_family':'LFQ intensity','sample_design_version_id':SD,'contrasts':[{'contrast_id':CID,'numerator_group':'Treatment','denominator_group':'WT'}]}}}
        current_design_bad=False
        def version():
            d=str(uuid4());metas[d]={'created_by':A,'project_id':P,'derived_version_id':d,'source_result_id':S,'source_manifest_sha256':parent['manifest_sha256'],
                'source_binding':{'source_metadata_sha256':saved.digest(parent)},'source_binding_sha256':'c'*64,'parameters':copy.deepcopy(PARAM),'parameters_sha256':h.sha(PARAMS),
                'status':'CLASSIFIED','DE_members_evaluated':True,'manifest_sha256':'d'*64,'core':saved.core(),
                'artifacts':[{'name':n,**pin(body)} for n,body in [('selected-DE.tsv',SELECTED),('classification-parameters.tsv',PARAMS)]]};return d
        async def meta(repo,actor,project,d,budget):
            nonlocal current_design_bad
            if (actor,project)!=(A,P) or d not in metas:raise Fault(404,'not_found')
            current_design_bad=d in bad_design;return copy.deepcopy(metas[d])
        async def artifact(repo,actor,project,d,name,budget):
            await meta(repo,actor,project,d,budget)
            return PARAMS if name=='classification-parameters.tsv' else b'bad\n' if d in bad_selected else SELECTED
        async def result(repo,actor,project,budget,*,result_id=None,**unused):
            if (actor,project,result_id)!=(A,P,S):raise Fault(404,'not_found')
            return copy.deepcopy(parent)
        async def samples(repo,actor,project,dataset,family,budget,request_id=None):
            self.assertEqual((actor,project,dataset,family,request_id),(A,P,DS,'LFQ intensity',DR));rows=copy.deepcopy(DESIGN_ROWS)
            if current_design_bad:rows[0]['replicate_type']='technical'
            return {'record':{'sample_design_version_id':SD,'request_id':DR,'design':{'rows':rows}}}
        async def transaction(repo,actor,budget,operation,*,writing=False):
            self.assertFalse(writing)
            async def query(sql,args=(),write=False):
                self.assertFalse(write);queries.append(sql)
                if sql.startswith('SELECT project_id FROM'):return (P,)
                self.assertIn('SELECT matrix_bytes,octet_length(matrix_bytes)',sql);self.assertNotIn('FOR UPDATE',sql)
                self.assertEqual(tuple(map(str,args)),(JOB,A,P));body=b'corrupt\n' if bad_matrix else MATRIX
                return (body,len(body),h.sha(body),saved.canonical(parent['input_manifest']))
            return await operation(query)
        async def auth(req):
            actor=json.loads(base64.urlsafe_b64decode(req.headers['authorization'].split('.')[1]+'=='))['sub']
            return httpx.Response(200,json={'id':actor,'role':'authenticated','aud':'authenticated','is_anonymous':False,'email':'synthetic@example.invalid','email_confirmed_at':'2026-01-01T00:00:00Z'})
        def native(executor,inputs=None,*,cancelled=lambda:False):
            cfg=executor._config;natives.append(cfg.render_id);self.assertFalse(cancelled());self.assertEqual(inputs['matrix.tsv'],MATRIX);self.assertEqual(inputs['selected-DE.tsv'],SELECTED)
            argv=executor._invocation(SimpleNamespace(NATIVE_EXECUTABLE=Path('/usr/lib/R/bin/exec/R')),cfg.staging_root/'synthetic.partial')
            self.assertEqual(argv[:3],['/usr/lib/R/bin/exec/R','--vanilla','--slave']);self.assertEqual([Path(x).name for x in argv[5:]],list(r.INPUT_NAMES)+['output'])
            return r.Output(outputs(inputs),{'all_eight_outputs_verified':True,'binding_sha256':cfg.binding_sha256,'profile_sha256':h.sha(cfg.profile_json)})
        original_publish=h.publish
        def publish(cfg,binding,bodies,rid):
            if binding.context['derived_version_id'] in partial:
                q=cfg.publish_root/('.render-'+rid+'.partial');q.mkdir(mode=0o700);(q/'heatmap.svg').write_bytes(bodies['heatmap.svg']);raise Fault(503,'r_heatmap_unavailable')
            return original_publish(cfg,binding,bodies,rid)
        async def spawn(controller):
            self.assertEqual(controller._argv(),(shared.PYTHON,'-I','-B',str(BACKEND.parent/'controls/heatmap_service_startup.py'),'--once'));spawned.append(controller.render_id)
            async def child(proc,wire):
                n=h.parse_notification(wire);phase='unknown';code=1
                try:phase=await h.execute(controller.render_config,n,controller.reader,native_pins=(),verify=lambda:None);code=0
                except Exception:
                    if n['derived_version_id'] not in partial:
                        import traceback
                        traceback.print_exc()
                reply={'schemaVersion':1,'render_id':n['job_id'],'request_id':n['request_id'],'binding_sha256':n['binding_sha256'],'state':phase,
                    'disposition':'confirmed' if code==0 else 'unknown','teardown':'confirmed' if code==0 else 'unknown','loop_closed':True,
                    'pending_tasks_seen':False,'async_resources_remaining':False,'interrupted':False,'exit_code':code}
                proc.stdout.feed_data(shared.canonical(reply)+b'\n');proc.stdout.feed_eof();proc.stderr.feed_eof();proc.returncode=code;proc.event.set()
            return Process(child)
        def intent(controller,value,*,job_key):
            fd=os.open(controller.config.control,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            try:
                f=os.open(value[job_key]+'.launch.once',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
                try:wire=shared.canonical(value)+b'\n';self.assertEqual(os.write(f,wire),len(wire));os.fsync(f)
                finally:os.close(f)
                os.fsync(fd)
            finally:os.close(fd)
        with tempfile.TemporaryDirectory(prefix='heatmap-finite-') as temporary,ExitStack() as stack:
            root=Path(temporary);roots=[]
            for name in ['control','staging','published']:q=root/name;q.mkdir(mode=0o700);roots.append(q)
            control,staging,published=roots
            profile={'contract':'r_heatmap_profile_v0.1','library_root':'/usr/local/lib/R/site-library','library_metadata':[dict(path=p,bytes=n,sha256=s) for p,n,s in r.KNOWN_METADATA],
                'font':r.KNOWN_FONT,'versions':{'R':'4.6.1','pheatmap':'1.0.13','digest':'0.6.39'},'adapter':pin((BACKEND/'cyomics_projects/heatmap_runner.py').read_bytes())}
            cfg=h.Config(shared.Config(True,BACKEND.parent/'controls/heatmap_service_startup.py',BACKEND,control,'a'*64,'b'*64,lambda:None),staging,published,
                h.canonical(profile),h.canonical({'project_id':P,'source_result_id':S,'parameters':PARAM}))
            def client():
                app=create_app(auth_transport=httpx.MockTransport(auth),pool=object(),anon_key_loader=lambda:token(role='anon'),classification_results_enabled=True,
                    heatmap_read_config=plots.Config(True,published,1000),heatmap_dispatch_config=cfg);apps.append(app)
                cli=httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:57430');clients.append(cli);return cli,app.state.heatmap_dispatch
            headers={'authorization':'Bearer '+token(),'origin':'http://127.0.0.1:4206','sec-fetch-site':'same-origin','x-cyomics-request':'1'}
            path=lambda d:'/api/v1/projects/'+P+'/derived-classifications/'+d+'/heatmap/render'
            async def settle(k):
                task=k._monitor
                if task is not None:await task
            network=stack.enter_context(patch.object(socket.socket,'connect',side_effect=AssertionError('network forbidden')));popen=stack.enter_context(patch.object(subprocess,'Popen',side_effect=AssertionError('native forbidden')));aspawn=stack.enter_context(patch('asyncio.create_subprocess_exec',side_effect=AssertionError('native forbidden')))
            stack.enter_context(patch.object(saved.ClassificationRepository,'read_version',meta));stack.enter_context(patch.object(saved.ClassificationRepository,'read_artifact',artifact));stack.enter_context(patch.object(ResultReader,'read',result));stack.enter_context(patch.object(SampleDesignRepository,'read',samples));stack.enter_context(patch.object(saved.admission.DifferentialRepository,'_transaction',transaction))
            stack.enter_context(patch.object(saved,'validate_dto',side_effect=lambda x:x));stack.enter_context(patch.object(plots,'response_value',side_effect=lambda *a:None))
            stack.enter_context(patch.object(shared.Controller,'_sources',lambda self:None));stack.enter_context(patch.object(shared.Controller,'_spawn',spawn));stack.enter_context(patch.object(shared.Controller,'_pin',side_effect=lambda proc:os.open('/dev/null',os.O_RDONLY)));stack.enter_context(patch.object(shared.Controller,'_intent',intent));stack.enter_context(patch.object(r.HeatmapRunner,'run',native));stack.enter_context(patch.object(h,'publish',publish))
            original_directory=plots.previous.FileProvider._directory
            def directory(provider,fd,*,private=False,owners=None):
                if private:return original_directory(provider,fd,private=True,owners=owners)
                info=os.fstat(fd);self.assertTrue(stat.S_ISDIR(info.st_mode));return plots.previous.identity(info)
            # Explicit physical ancestor double: test may run in a normal /tmp checkout.
            # Every private directory and all file descriptors/bytes remain original checks.
            stack.enter_context(patch.object(plots.previous.FileProvider,'_directory',directory))
            groups=[]
            try:
                cli,k=client();d=version();rq=str(uuid4());resp=await cli.get(path(d)+'?request_id='+rq,headers=headers);self.assertEqual(resp.status_code,404,resp.text);self.assertEqual(len(spawned),0)
                resp=await cli.post(path(d),json={'request_id':rq},headers=headers);self.assertEqual(resp.status_code,201,resp.text);rid=resp.json()['render_id'];await settle(k)
                resp=await cli.get(path(d)+'?request_id='+rq,headers=headers);self.assertEqual(resp.status_code,200,resp.text);self.assertEqual(resp.json()['state'],'ready',resp.text)
                self.assertEqual(len(resp.json()['descriptor']['artifacts']),8);self.assertEqual(set(x.name for x in (published/d).iterdir()),set(plots.NAMES)|set(plots.INPUTS)|{'metadata.json'})
                resp=await cli.get(path(d).removesuffix('/render'),headers=headers);self.assertEqual(resp.status_code,200,resp.text);self.assertEqual((len(spawned),len(natives)),(1,1))
                resp=await cli.post(path(d),json={'request_id':str(uuid4())},headers=headers);self.assertEqual(resp.status_code,200);self.assertEqual(resp.json()['render_id'],rid)
                cli2,k2=client();resp=await cli2.post(path(d),json={'request_id':rq},headers=headers);self.assertEqual(resp.status_code,200);self.assertEqual((len(spawned),len(natives)),(1,1))
                groups.append('saved_matrix_readonly_original_bound_samples_and_provenance_original_validators_atomic13_files_201_ready_and_alias_restart_no_R')
                d2=version();foreign=dict(headers,authorization='Bearer '+token(B));resp=await cli2.post(path(d2),json={'request_id':str(uuid4())},headers=foreign);self.assertEqual(resp.status_code,404)
                bad_matrix.add(d2);resp=await cli2.post(path(d2),json={'request_id':str(uuid4())},headers=headers);self.assertEqual(resp.status_code,503,resp.text);bad_matrix.clear()
                bad_design.add(d2);resp=await cli2.post(path(d2),json={'request_id':str(uuid4())},headers=headers);self.assertEqual(resp.status_code,503,resp.text);bad_design.clear()
                bad_selected.add(d2);resp=await cli2.post(path(d2),json={'request_id':str(uuid4())},headers=headers);self.assertEqual(resp.status_code,503,resp.text);bad_selected.clear()
                resp=await cli2.post(path(d2),json={'request_id':str(uuid4()),'samples':[]},headers=headers);self.assertEqual(resp.status_code,400);self.assertEqual(len(natives),1)
                self.assertTrue(all(sql.startswith('SELECT ') for sql in queries));groups.append('Owner_source_matrix_pin_sample_design_and_selected_member_pin_refusals_readonly_no_claim_or_override')
                d3=version();partial.add(d3);rq3=str(uuid4());resp=await cli2.post(path(d3),json={'request_id':rq3},headers=headers);self.assertEqual(resp.status_code,201,resp.text);await settle(k2)
                resp=await cli2.get(path(d3)+'?request_id='+rq3,headers=headers);self.assertEqual(resp.json()['state'],'unknown');self.assertFalse((published/d3).exists());self.assertTrue(k2._capacity.unknown)
                cli3,k3=client();resp=await cli3.post(path(d3),json={'request_id':str(uuid4())},headers=headers);self.assertEqual(resp.status_code,504);self.assertEqual(len(natives),2)
                self.assertIs(h.Controller.__mro__[1],vg.Controller);self.assertEqual(vg.Controller._contract_name,'r_volcano_render_v0.1');self.assertEqual(vg.Controller._error_prefix,'r_volcano')
                sentinel=object()
                with patch.object(vg,'prepare',return_value=sentinel) as default:self.assertIs(vg.Controller._prepare(sentinel),sentinel);default.assert_called_once_with(sentinel)
                state=raw._State();operation=object()
                class Loop:
                    def set_exception_handler(self,handler):pass
                    def run_until_complete(self,coro):return 'ready'
                def install(fence):fence.saved=[(raw.signal.SIGINT,None),(raw.signal.SIGTERM,None)]
                out=io.StringIO();activation=Mock(side_effect=AssertionError('factory forbidden'))
                with ExitStack() as pure,redirect_stdout(out):
                    pure.enter_context(patch.object(h,'_State',return_value=state));pure.enter_context(patch.object(h,'prepare',side_effect=lambda c:c));pure.enter_context(patch.object(h,'_eligible_context',return_value=True));pure.enter_context(patch.object(raw._Signals,'install',install));signal_mock=pure.enter_context(patch.object(raw.signal,'signal',side_effect=OSError('mock restore failure')));pure.enter_context(patch.object(h.asyncio,'new_event_loop',return_value=Loop()));pure.enter_context(patch.object(h,'_from_factory',new=Mock(return_value=operation)));pure.enter_context(patch.object(h,'_settle_and_close',return_value=(False,False,True)))
                    wire={'owner_id':A,'project_id':P,'request_id':rq,'job_id':rid,'derived_version_id':d,'binding_sha256':'a'*64};code=h.main_from_server(shared.canonical(wire)+b'\n',config=cfg,activate_factory=activation,verify=lambda:None)
                payload=json.loads(out.getvalue());self.assertEqual(code,1);self.assertTrue(state.cleanup_error);self.assertEqual(payload['teardown'],'unknown');self.assertEqual(signal_mock.call_count,2);activation.assert_not_called()
                groups.append('unknown_partial_publish_restart_fence_and_cleanup_error_fail_closed_original_volcano_default_hooks_retained')
                self.assertEqual((network.call_count,popen.call_count,aspawn.call_count),(0,0,0))
                print(json.dumps({'single_finite_combination':'PASS','groups':groups,'real_R_DB_Auth_Factory_signals':0,'mock_boundaries':['upstream repository and DTO persistence proofs','physical ancestor permissions and process launcher','native R output'],'original_exercised':['readonly matrix helper SQL/Owner call shape','bound_samples/design/preset binding','four input and eight output validators','13-file publisher and OwnerReader/FileProvider','shared durable controller/fence/supervision','original Signals.close with restoration mocks'],'old_cases_rerun':0}))
            finally:
                for cli in clients:await cli.aclose()
                for app in apps:app.state.complete_imports.close();await app.state.authority.close()

if __name__=='__main__':unittest.main()
