"""One synthetic Auth/Owner/provider composition case; no real DB/HTTP/R/images."""
from contextlib import ExitStack
from pathlib import Path
from uuid import UUID
from unittest.mock import patch
import base64,copy,hashlib,json,os,socket,subprocess,sys,tempfile,time,unittest

BACKEND=Path(__file__).resolve().parents[1]
BASELINE=Path('/home/user01/Cy_Omics_v2.0/source/CyOmics_V2-reviewed-c4e9b66-bundle/backend')
sys.path.insert(0,str(BACKEND if (BACKEND/'cyomics_projects/__init__.py').is_file() else BASELINE))
import cyomics_projects
cyomics_projects.__path__.insert(0,str(BACKEND/'cyomics_projects'))
import httpx
from cyomics_projects.app import create_app
from cyomics_projects import volcano_owner_read as v,de_classification_repository as saved,differential_jobs as original
from cyomics_projects.settings import Fault,EXPECTED_ISSUER

A='11111111-1111-4111-8111-111111111111';B='22222222-2222-4222-8222-222222222222'
P='33333333-3333-4333-8333-333333333333';WRONG='99999999-9999-4999-8999-999999999999'
V='44444444-4444-4444-8444-444444444444';ORIGIN='55555555-5555-4555-8555-555555555555'
R='66666666-6666-4666-8666-666666666666';Q='77777777-7777-4777-8777-777777777777';M='88888888-8888-4888-8888-888888888888'

def token(subject=A,role='authenticated'):
    enc=lambda x:base64.urlsafe_b64encode(json.dumps(x).encode()).decode().rstrip('=')
    now=int(time.time())
    return '.'.join((enc({'alg':'HS256'}),enc({'sub':subject,'role':role,'iss':EXPECTED_ISSUER,'aud':'authenticated','iat':now-5,'exp':now+120,'is_anonymous':False}),enc('offline-only')))

def fixture():
    parent={'result_id':R,'project_id':P,'created_by':A,'manifest_sha256':'a'*64}
    p={'schema_version':'de_classification_v0.1','input_scale':'log2','adjusted_p_max':'0.05','absolute_log2fc_min':'1'}
    params=saved.c.parameters(('key\tvalue\n'+''.join(k+'\t'+p[k]+'\n' for k in saved.c.PARAM_KEYS)).encode())
    raw={n:('synthetic original '+n+'\n').encode() for n in saved.c.OUTPUTS};raw['classification-parameters.tsv']=params.body
    artifacts=[{'name':n,'bytes':len(raw[n]),'sha256':saved.c.sha(raw[n])} for n in saved.c.OUTPUTS]
    binding={'owner_id':A,'project_id':P,'source_result_id':R,'source_manifest_sha256':'a'*64,'source_metadata_sha256':saved.digest(parent)}
    meta={'schemaVersion':1,'contract':saved.CONTRACT,'derived_version_id':V,'request_id':Q,'project_id':P,'created_by':A,'source_result_id':R,
        'source_manifest_sha256':'a'*64,'source_binding_sha256':saved.digest(binding),'source_binding':binding,'parameters':p,'parameters_sha256':saved.c.sha(params.body),
        'core':saved.core(),'status':'CLASSIFIED','DE_members_evaluated':True,'counts':{'Up':1,'Down':1,'NS':3,'not_testable':2,'not_evaluated':0,'selected_DE_rows':2},
        'artifacts':artifacts,'manifest_sha256':saved.digest(artifacts),'member_set':{'member_set_id':M,'derived_version_id':V,
            'selected_artifact_sha256':next(x['sha256'] for x in artifacts if x['name']=='selected-DE.tsv'),'member_count':2},
        'created_at':'2026-01-01T00:00:00.000000Z','source_analysis_ready':False}
    origin=copy.deepcopy(meta);origin['derived_version_id']=ORIGIN;origin['request_id']=ORIGIN;origin['member_set']['derived_version_id']=ORIGIN
    saved.validate_dto(meta);saved.validate_dto(origin)
    # Opaque bytes exercise storage/headers only; never presented as actual images.
    bodies={n:('synthetic opaque '+n+'\n').encode() for n in v.NAMES};bodies['volcano.png']+=b'x'*1048576
    pins=[{'name':n,'bytes':len(bodies[n]),'sha256':saved.c.sha(bodies[n]),'media_type':v.MEDIA[n]} for n in v.NAMES]
    registration={'schemaVersion':1,'contract':'r_volcano_saved_v0.1','project_id':P,'created_by':A,'source_result_id':R,'derived_version_id':V,
        'source_metadata_sha256':saved.digest(parent),'source_manifest_sha256':parent['manifest_sha256'],'derived_metadata_sha256':saved.digest(meta),
        'derived_manifest_sha256':meta['manifest_sha256'],'source_binding_sha256':meta['source_binding_sha256'],'parameters_sha256':meta['parameters_sha256'],
        'render_source_derived_id':ORIGIN,'render_source_metadata_sha256':saved.digest(origin),'render_script':v.SCRIPT,'inputs':v.input_pins(meta),'artifacts':pins}
    return parent,meta,origin,bodies,registration

class OwnerReadCombination(unittest.IsolatedAsyncioTestCase):
    async def test_one_finite_combination(self):
        parent,meta,origin,bodies,registration=fixture();events=[];scenarios=[];state={'timeout':False};apps=[]
        audit=Path('/home/user01/Cy_Omics_v2.0/audit/VOLCANO-OWNER-READ-001/evidence')
        temp=tempfile.TemporaryDirectory(prefix='synthetic-provider-',dir=audit);root=Path(temp.name);folder=root/V;folder.mkdir(mode=0o700)
        def put(name,raw):
            p=folder/name
            if p.is_symlink():p.unlink()
            if p.exists():p.chmod(0o600)
            p.write_bytes(raw);p.chmod(0o444)
        def reg(value):put('metadata.json',saved.canonical(value).encode('utf8'))
        for n,raw in bodies.items():put(n,raw)
        reg(registration)
        async def auth(request):
            subject=json.loads(base64.urlsafe_b64decode(request.headers['authorization'].split('.')[1]+'=='))['sub']
            events.append('auth:'+subject)
            return httpx.Response(200,json={'id':subject,'aud':'authenticated','role':'authenticated','is_anonymous':False,
                'email':'offline@example.invalid','email_confirmed_at':'2025-01-01T00:00:00Z'})
        async def transaction(repo,subject,budget,operation,*,writing=False):
            self.assertFalse(writing);events.append('owner_transaction:'+subject)
            if state['timeout']:raise TimeoutError()
            async def query(sql,args,**kwargs):
                self.assertFalse(kwargs.get('write',False));self.assertTrue(sql.startswith('SELECT project_id FROM cyomics_app.projects WHERE owner_id='))
                return (UUID(P),) if tuple(map(str,args))==(A,P) else None
            return await operation(query)
        async def get_meta(repo,query,subject,project,**kwargs):
            version=kwargs['version_id'];events.append('derived:'+version)
            if version==V:return copy.deepcopy(meta),{}
            if version==ORIGIN:return copy.deepcopy(origin),{}
            return None
        async def parent_read(reader,subject,project,budget,**kwargs):
            self.assertEqual((subject,project,kwargs),(A,P,{'result_id':R}));events.append('parent');return copy.deepcopy(parent)
        original_read=v.FileProvider._read
        def file_read(provider,version,check,expected=None):
            events.append('cache_metadata' if expected is None else 'cache_artifacts')
            return original_read(provider,version,check,expected)
        def app(config,enabled=True):
            value=create_app(auth_transport=httpx.MockTransport(auth),pool=object(),anon_key_loader=lambda:token(role='anon'),
                classification_results_enabled=enabled,volcano_read_config=config);apps.append(value);return value
        headers={'authorization':'Bearer '+token(),'origin':'http://127.0.0.1:4206','sec-fetch-site':'same-origin','x-cyomics-request':'1'}
        path='/api/v1/projects/'+P+'/derived-classifications/'+V+'/volcano'
        try:
            with ExitStack() as stack:
                network=stack.enter_context(patch.object(socket.socket,'connect',side_effect=AssertionError('network forbidden')))
                native=stack.enter_context(patch.object(subprocess,'Popen',side_effect=AssertionError('native forbidden')))
                stack.enter_context(patch.object(original.DifferentialRepository,'_transaction',transaction))
                stack.enter_context(patch.object(saved.ClassificationRepository,'_get',get_meta))
                stack.enter_context(patch.object(v.ResultReader,'read',parent_read))
                stack.enter_context(patch.object(v.FileProvider,'_read',file_read))
                writer=stack.enter_context(patch.object(saved.ClassificationRepository,'save_verified',side_effect=AssertionError('write forbidden')))
                for config in (None,v.Config(False,root)):
                    off=app(config)
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=off),base_url='http://127.0.0.1:57430') as client:
                        events.clear();response=await client.get(path,headers=headers);self.assertEqual(response.status_code,404);self.assertEqual(events,[])
                scenarios.append('default_disabled_preserves_original_fallback_no_IO')
                with self.assertRaises(Exception):app(v.Config(True,root),False)
                active=app(v.Config(True,root))
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=active),base_url='http://127.0.0.1:57430') as client:
                    events.clear();response=await client.get(path,headers=headers);self.assertEqual(response.status_code,200,response.text);descriptor=response.json()
                    expected={'schemaVersion','contract','project_id','created_by','source_result_id','derived_version_id','source_metadata_sha256',
                        'source_manifest_sha256','derived_metadata_sha256','derived_manifest_sha256','source_binding_sha256','parameters_sha256','artifacts'}
                    self.assertEqual(set(descriptor),expected);self.assertEqual(len(descriptor),13);self.assertEqual(descriptor['contract'],'r_volcano_view_v0.1')
                    self.assertEqual(descriptor['derived_metadata_sha256'],saved.digest(meta));self.assertEqual(descriptor['artifacts'],registration['artifacts'])
                    self.assertNotIn('render_source_derived_id',descriptor);self.assertLessEqual(len(response.content),32768)
                    self.assertLess(events.index('derived:'+V),events.index('cache_metadata'));self.assertLess(events.index('parent'),events.index('cache_metadata'))
                    self.assertLess(events.index('derived:'+ORIGIN),events.index('cache_artifacts'));scenarios.append('Owner_parent_before_cache_and_origin_before_bytes_exact13')
                    for name in v.NAMES:
                        response=await client.get(path+'/artifacts/'+name,headers=headers)
                        self.assertEqual(response.status_code,200,response.text[:300]);self.assertEqual(response.content,bodies[name])
                        self.assertEqual(response.headers['content-type'],v.MEDIA[name]);self.assertEqual(response.headers['content-length'],str(len(bodies[name])))
                        self.assertEqual(response.headers['x-cyomics-file-sha256'],saved.c.sha(bodies[name]))
                        self.assertEqual(response.headers['content-disposition'],('inline' if name=='volcano.png' else 'attachment')+'; filename="'+name+'"')
                        self.assertEqual(response.headers['cache-control'],'no-store');self.assertEqual(response.headers['x-content-type-options'],'nosniff');self.assertEqual(response.headers['referrer-policy'],'no-referrer')
                    scenarios.append('all5_exact_bytes_MIME_pins_headers_PNG_inline_over1MiB')
                    for request_headers,url in (({k:v for k,v in headers.items() if k!='authorization'},path),
                        (dict(headers,authorization='Bearer '+token(B)),path),(headers,path.replace(P,WRONG,1))):
                        events.clear();response=await client.get(url,headers=request_headers)
                        self.assertIn(response.status_code,(401,404));self.assertFalse(any(e.startswith('cache_') for e in events));self.assertLessEqual(len(response.content),4096)
                        if response.status_code==404:self.assertEqual(response.json(),{'error':{'code':'not_found'}})
                    scenarios.append('anonymous_foreign_Owner_project_never_touch_cache')
                    folder.rename(root/'hidden')
                    response=await client.get(path,headers=headers);self.assertEqual((response.status_code,response.json()),(404,{'error':{'code':'not_found'}}))
                    (root/'hidden').rename(folder);scenarios.append('missing_plot_not_found_no_demo_compute')
                    for key,value in (('derived_metadata_sha256','0'*64),('render_script',{'bytes':13932,'sha256':'0'*64}),('created_by',B)):
                        bad=copy.deepcopy(registration);bad[key]=value;reg(bad)
                        response=await client.get(path,headers=headers);self.assertEqual(response.status_code,503);self.assertEqual(response.json(),{'error':{'code':'r_volcano_unavailable'}})
                    reg(registration);scenarios.append('stale_binding_R1_pin_wrong_Owner_registration_refused')
                    old=origin['parameters_sha256'];origin['parameters_sha256']='0'*64
                    response=await client.get(path,headers=headers);self.assertEqual(response.status_code,503);origin['parameters_sha256']=old
                    scenarios.append('render_source_context_cannot_be_relabelled')
                    for big,total in ((v.MAX_FILE+1,False),(v.MAX_FILE,True)):
                        bad=copy.deepcopy(registration)
                        for pin in (bad['artifacts'] if total else bad['artifacts'][:1]):pin['bytes']=big
                        reg(bad);response=await client.get(path,headers=headers);self.assertEqual(response.status_code,503)
                    reg(registration);scenarios.append('strict_single_and_total_caps_before_payload')
                    put('volcano.svg',b'x'*len(bodies['volcano.svg']))
                    response=await client.get(path+'/artifacts/volcano.png',headers=headers);self.assertEqual(response.status_code,503)
                    put('volcano.svg',bodies['volcano.svg']);scenarios.append('all5_hashes_checked_even_for_PNG_request')
                    file=folder/'volcano.svg';file.unlink();outside=root/'outside';outside.write_bytes(bodies['volcano.svg']);file.symlink_to(outside)
                    response=await client.get(path,headers=headers);self.assertEqual(response.status_code,503)
                    put('volcano.svg',bodies['volcano.svg']);(folder/'metadata.json').chmod(0o600)
                    response=await client.get(path,headers=headers);self.assertEqual(response.status_code,503);(folder/'metadata.json').chmod(0o444)
                    scenarios.append('symlink_and_mutable_metadata_refused')
                    for url in (path+'?url=https://example.invalid/x',path+'/artifacts/metadata.json',path+'/artifacts/volcano.pdf'):
                        response=await client.get(url,headers=headers);self.assertIn(response.status_code,(400,404))
                    response=await client.post(path,headers=headers);self.assertEqual(response.status_code,405)
                    state['timeout']=True;events.clear();response=await client.get(path,headers=headers)
                    self.assertEqual(response.status_code,504);self.assertEqual(response.json(),{'error':{'code':'r_volcano_read_timeout'}})
                    self.assertFalse(any(e.startswith('cache_') for e in events));state['timeout']=False
                    scenarios.append('strict_route_no_wire_path_POST_and_timeout_fail_closed')
                self.assertEqual(writer.call_count,0);self.assertEqual(network.call_count,0);self.assertEqual(native.call_count,0)
            print(json.dumps({'single_combination':'PASS','scenarios':scenarios,'real_DB_HTTP_Auth_R':0,'old_suites':0,'fixture':'opaque synthetic bytes; no image/science case replay'}))
        finally:
            for application in apps:application.state.complete_imports.close();await application.state.authority.close()
            folder.chmod(0o700);root.chmod(0o700);temp.cleanup()

if __name__=='__main__':unittest.main()
