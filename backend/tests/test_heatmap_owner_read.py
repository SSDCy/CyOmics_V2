"""One finite synthetic Owner/design/cache composition; no real DB/Auth/R/cache."""
from contextlib import ExitStack
from datetime import datetime,timezone
from pathlib import Path
from uuid import UUID
from unittest.mock import patch
import base64,copy,json,math,socket,struct,subprocess,sys,tempfile,time,unittest,zlib

BACKEND=Path(__file__).resolve().parents[1]
BASELINE=Path('/home/user01/Cy_Omics_v2.0/source/CyOmics_V2-reviewed-5df4fbc-bundle/backend')
sys.path.insert(0,str(BACKEND if (BACKEND/'cyomics_projects/__init__.py').is_file() else BASELINE))
import cyomics_projects
cyomics_projects.__path__.insert(0,str(BACKEND/'cyomics_projects'))
import httpx
from cyomics_projects.app import create_app
from cyomics_projects import heatmap_owner_read as v,de_classification_repository as saved,differential_jobs as original,sample_design as sd,sample_persistence as sp
from cyomics_projects.settings import Fault,EXPECTED_ISSUER
h=v.heatmap
A='11111111-1111-4111-8111-111111111111';OTHER='22222222-2222-4222-8222-222222222222';P='33333333-3333-4333-8333-333333333333'
V='44444444-4444-4444-8444-444444444444';DATASET='55555555-5555-4555-8555-555555555555';R='66666666-6666-4666-8666-666666666666';Q='77777777-7777-4777-8777-777777777777'
MEMBER='88888888-8888-4888-8888-888888888888';DESIGN='99999999-9999-4999-8999-999999999999';DREQ='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';PROCESSED='bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';CID='cccccccc-cccc-4ccc-8ccc-cccccccccccc'
def token(subject=A,role='authenticated'):
    enc=lambda x:base64.urlsafe_b64encode(json.dumps(x).encode()).decode().rstrip('=')
    now=int(time.time());return '.'.join((enc({'alg':'HS256'}),enc({'sub':subject,'role':role,'iss':EXPECTED_ISSUER,'aud':'authenticated','iat':now-5,'exp':now+120,'is_anonymous':False}),enc('synthetic-only')))
def tsv(header,rows):return v._tsv([header]+list(rows))
def png():
    def chunk(k,b):return struct.pack('>I',len(b))+k+b+struct.pack('>I',zlib.crc32(k+b)&0xffffffff)
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',2400,1350,8,2,0,0,0))+chunk(b'IDAT',zlib.compress((b'\0'+b'\0'*7200)*1350))+chunk(b'IEND',b'')

def fixture():
    columns=[];design_rows=[];samples=[]
    for i in range(1,7):
        label='样本'+str(i);group='Treatment' if i<=3 else 'WT';ordinal=100+i*2
        columns.append({'ordinal':ordinal,'name_raw':'LFQ intensity '+label,'sample_label_raw':label,'quant_family':'LFQ intensity'})
        design_rows.append({'column_ordinal':ordinal,'name_raw':'LFQ intensity '+label,'sample_label_raw':label,'group':group,'condition':'synthetic','control':'no' if i<=3 else 'yes','replicate':str((i-1)%3+1),'replicate_type':'biological','confirmed':True})
        samples.append(('s'+str(i),str(ordinal),label,'g1' if i<=3 else 'g2',group,'r'+str(i),'biological'))
    design={'schemaVersion':1,'dataset_version_id':DATASET,'quant_family':'LFQ intensity','rows':design_rows}
    source=sd.bind_trusted_source(DATASET,columns);sd.validate_design(design,source)
    matrix=tsv(h.MATRIX_HEADER,[('10','A;A-iso','2.00','4e0','6','8','10','12'),('20','B;B-iso','10','8','6','4','2','0')])
    selected_rows=[('10','A;A-iso',CID,'testable','','1','2','','1','1','4','4','0.001','0.01','3','3','3','3','Up','TRUE'),('20','B;B-iso',CID,'testable','','-1','0.5','','1','-1','4','4','0.001','0.01','3','3','3','3','Down','TRUE')]
    selected=tsv(h.SELECTED_HEADER,selected_rows);sample_bytes=tsv(h.SAMPLES_HEADER,samples)
    design_bytes=tsv(('sample_key','group_key','replicate_key','replicate_type','confirmed'),[(s[0],s[3],s[5],'biological','true') for s in samples])
    preset=v._tsv([('key','value'),('schema_version','limma_core_v0.1'),('input_scale','log2'),('upstream_transform','positive_log2'),('upstream_zero_blank','NA'),('pseudocount','0'),('normalization','none'),('imputation','none'),('replicate_type','biological'),('design_confirmed','true'),('contrast_id',CID),('numerator','g1'),('denominator','g2')])
    parent={'result_id':R,'project_id':P,'created_by':A,'manifest_sha256':'a'*64,'input_manifest':{'matrix':v.pin(matrix),'design':v.pin(design_bytes),'preset':v.pin(preset)},
        'selection':{'processed_dataset_version_id':PROCESSED,'sample_design_version_id':DESIGN,'sample_design_request_id':DREQ,'replicate_model':'independent_biological','contrast':{'schemaVersion':1,'dataset_version_id':DATASET,'quant_family':'LFQ intensity','sample_design_version_id':DESIGN,'contrasts':[{'contrast_id':CID,'numerator_group':'Treatment','denominator_group':'WT'}]}}}
    params_dict={'schema_version':'de_classification_v0.1','input_scale':'log2','adjusted_p_max':'0.05','absolute_log2fc_min':'1'}
    params=saved.c.parameters(tsv(('key','value'),[(k,params_dict[k]) for k in saved.c.PARAM_KEYS]))
    class_bodies={n:('synthetic old '+n+'\n').encode() for n in saved.c.OUTPUTS};class_bodies['selected-DE.tsv']=selected;class_bodies['classification-parameters.tsv']=params.body
    artifacts=[{'name':n,**v.pin(class_bodies[n])} for n in saved.c.OUTPUTS];binding={'owner_id':A,'project_id':P,'source_result_id':R,'source_manifest_sha256':'a'*64,'source_metadata_sha256':saved.digest(parent)}
    meta={'schemaVersion':1,'contract':saved.CONTRACT,'derived_version_id':V,'request_id':Q,'project_id':P,'created_by':A,'source_result_id':R,'source_manifest_sha256':'a'*64,
        'source_binding':binding,'source_binding_sha256':saved.digest(binding),'parameters':params_dict,'parameters_sha256':saved.c.sha(params.body),'core':saved.core(),'status':'CLASSIFIED','DE_members_evaluated':True,
        'counts':{'Up':1,'Down':1,'NS':0,'not_testable':0,'not_evaluated':0,'selected_DE_rows':2},'artifacts':artifacts,'manifest_sha256':saved.digest(artifacts),
        'member_set':{'member_set_id':MEMBER,'derived_version_id':V,'selected_artifact_sha256':v.pin(selected)['sha256'],'member_count':2},'created_at':'2026-01-01T00:00:00.000000Z','source_analysis_ready':False}
    saved.validate_dto(meta)
    context={'project_id':P,'created_by':A,'source_result_id':R,'source_metadata_sha256':saved.digest(parent),'source_manifest_sha256':parent['manifest_sha256'],'derived_version_id':V,'derived_metadata_sha256':saved.digest(meta),'derived_manifest_sha256':meta['manifest_sha256'],'source_binding_sha256':meta['source_binding_sha256'],'parameters_sha256':meta['parameters_sha256']}
    three=[{'name':n,**v.pin(b)} for n,b in (('matrix.tsv',matrix),('samples.tsv',sample_bytes),('selected-DE.tsv',selected))]
    provenance=saved.canonical({'schemaVersion':1,'contract':'r_heatmap_provenance_v0.1',**context,'inputs':three}).encode()
    value=h.validate_inputs(matrix,selected,sample_bytes,provenance)
    sd_value=math.sqrt(14);means=(7,5);raw_rows=value.chosen
    map_rows=[];z_rows=[];all_z=[]
    for raw,sel,mean in zip(raw_rows,selected_rows,means):
        zs=[(float(x)-mean)/sd_value for x in raw[2:]];all_z.extend(zs)
        map_rows.append((raw[0],raw[1],sel[18],'6',format(mean,'.17g'),format(sd_value,'.17g'),'scaled'));z_rows.append(tuple(raw[:2])+tuple(format(x,'.17g') for x in zs))
    audit=dict(h.FIXED,input_rows='2',selected_rows='2',samples='6',observed_cells='12',missing_cells='0',plotted_observed_cells='12',plotted_missing_cells='0',scaled_rows='2',zero_sd_rows='0',all_missing_rows='0',insufficient_observations_rows='0',color_limit=format(max([1]+list(map(abs,all_z))),'.17g'),R_version='R version 4.6.1 (synthetic cache)',pheatmap_version='1.0.13')
    inputs={'matrix.tsv':matrix,'provenance.json':provenance,'samples.tsv':sample_bytes,'selected-DE.tsv':selected}
    for prefix,b in (('matrix',matrix),('selected_DE',selected),('samples',sample_bytes),('provenance',provenance)):audit[prefix+'_bytes']=str(len(b));audit[prefix+'_sha256']=saved.c.sha(b)
    bodies={'R-session.txt':b'R_VERSION\tR version 4.6.1 (synthetic cache)\nPHEATMAP_VERSION\t1.0.13\nDIGEST_VERSION\t0.6.39\nR version 4.6.1 (synthetic cache)\nloaded via a namespace: pheatmap_1.0.13 digest_0.6.39\n',
        'heatmap-audit.tsv':tsv(('key','value'),[(k,audit[k]) for k in h.AUDIT_KEYS]),'heatmap-matrix.tsv':tsv(h.MATRIX_HEADER,raw_rows),'heatmap-row-map.tsv':tsv(h.ROW_MAP_HEADER,map_rows),
        'heatmap-samples.tsv':sample_bytes,'heatmap-zscore.tsv':tsv(h.MATRIX_HEADER,z_rows),'heatmap.png':png(),
        'heatmap.svg':b'<svg xmlns="http://www.w3.org/2000/svg" width="1152pt" height="648pt" viewBox="0 0 1152 648"><desc>synthetic '+b'x'*1048576+b'</desc><rect width="1152" height="648" fill="grey"/></svg>'}
    h.validate_outputs(value,bodies)
    registration={'schemaVersion':1,'contract':'r_heatmap_saved_v0.1',**context,'render_script':v.SCRIPT,'inputs':[{'name':n,**v.pin(inputs[n])} for n in v.INPUTS],
        'artifacts':[{'name':n,**v.pin(bodies[n]),'media_type':v.MEDIA[n]} for n in v.NAMES]}
    return parent,meta,source,design,inputs,bodies,registration

class HeatmapOwnerFinite(unittest.IsolatedAsyncioTestCase):
    async def test_one_finite_combination(self):
        parent,meta,source,design,inputs,bodies,registration=fixture();events=[];scenarios=[];apps=[];state={}
        temp=tempfile.TemporaryDirectory(prefix='synthetic-',dir=Path(__file__).resolve().parents[3]/'evidence');root=Path(temp.name);folder=root/V;folder.mkdir(mode=0o700)
        def put(n,b):
            p=folder/n
            if p.is_symlink():p.unlink()
            if p.exists():p.chmod(0o600)
            p.write_bytes(b);p.chmod(0o444)
        def reg(r):put('metadata.json',saved.canonical(r).encode())
        for n,b in {**inputs,**bodies}.items():put(n,b)
        reg(registration)
        async def auth(request):
            subject=json.loads(base64.urlsafe_b64decode(request.headers['authorization'].split('.')[1]+'=='))['sub'];events.append('auth')
            return httpx.Response(200,json={'id':subject,'aud':'authenticated','role':'authenticated','is_anonymous':False,'email':'synthetic@example.invalid','email_confirmed_at':'2025-01-01T00:00:00Z'})
        async def transaction(repo,subject,budget,operation,writing=False):
            self.assertFalse(writing)
            if state.get('timeout'):raise TimeoutError()
            async def query(sql,args,**kwargs):
                self.assertFalse(kwargs.get('write',False))
                if type(repo) is v.SampleDesignRepository:
                    self.assertIn(' AND request_id=%s',sql);self.assertNotIn('ORDER BY',sql);self.assertEqual(tuple(map(str,args)),(P,DATASET,'LFQ intensity',DREQ));events.append('explicit_design_request')
                    d=copy.deepcopy(design)
                    if state.get('wrong_group'):d['rows'][0]['group']='unrelated'
                    raw=sp.canonical(d)
                    return (UUID(OTHER if state.get('wrong_version') else DESIGN),None,1,UUID(DREQ),'0'*64,raw,sp.sha(raw),datetime(2026,1,1,tzinfo=timezone.utc),sp.MODEL_CONTRACT,sp.VALIDATOR_SHA256)
                self.assertTrue(sql.startswith('SELECT project_id FROM cyomics_app.projects WHERE owner_id='));events.append('classification_Owner')
                return (UUID(P),) if tuple(map(str,args))==(A,P) else None
            return await operation(query)
        async def source_read(repo,query,subject,project,dataset):
            self.assertEqual((subject,project,dataset),(A,P,DATASET));events.append('design_Owner')
            if state.get('design_denied'):raise Fault(404,'dataset_not_found')
            return source
        async def get_meta(repo,query,subject,project,**kwargs):
            self.assertEqual(kwargs['version_id'],V);events.append('current_version');return copy.deepcopy(meta),{}
        async def parent_read(reader,subject,project,budget,**kwargs):
            self.assertEqual((subject,project,kwargs),(A,P,{'result_id':R}));events.append('parent_Owner');return copy.deepcopy(parent)
        original_read=v.FileProvider._read
        def cache_read(provider,version,check,expected=None):
            events.append('cache_metadata' if expected is None else 'cache_bodies');return original_read(provider,version,check,expected)
        def app(config,enabled=True):
            value=create_app(auth_transport=httpx.MockTransport(auth),pool=object(),anon_key_loader=lambda:token(role='anon'),classification_results_enabled=enabled,heatmap_read_config=config);apps.append(value);return value
        headers={'authorization':'Bearer '+token(),'origin':'http://127.0.0.1:4206','sec-fetch-site':'same-origin','x-cyomics-request':'1'}
        path='/api/v1/projects/'+P+'/derived-classifications/'+V+'/heatmap'
        try:
            with ExitStack() as stack:
                network=stack.enter_context(patch.object(socket.socket,'connect',side_effect=AssertionError('network forbidden')));native=stack.enter_context(patch.object(subprocess,'Popen',side_effect=AssertionError('native forbidden')))
                stack.enter_context(patch.object(original.DifferentialRepository,'_transaction',transaction));stack.enter_context(patch.object(saved.ClassificationRepository,'_get',get_meta));stack.enter_context(patch.object(v.ResultReader,'read',parent_read))
                stack.enter_context(patch.object(v.SampleDesignRepository,'_transaction',transaction));stack.enter_context(patch.object(v.SampleDesignRepository,'_source',source_read));stack.enter_context(patch.object(v.FileProvider,'_read',cache_read))
                writer=stack.enter_context(patch.object(saved.ClassificationRepository,'save_verified',side_effect=AssertionError('write forbidden')));design_writer=stack.enter_context(patch.object(v.SampleDesignRepository,'save',side_effect=AssertionError('write forbidden')))
                for config in (None,v.Config(False,root)):
                    off=app(config);self.assertFalse(any('/heatmap' in route.path for route in off.routes));events.clear()
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=off),base_url='http://127.0.0.1:57430') as client:
                        response=await client.get(path,headers=headers);self.assertEqual(response.status_code,404);self.assertEqual(events,[])
                with self.assertRaises(Exception):app(v.Config(True,root),False)
                scenarios.append('default_off_no_new_routes_or_cache_IO_original_classification_gate')
                active=app(v.Config(True,root))
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=active),base_url='http://127.0.0.1:57430') as client:
                    events.clear();response=await client.get(path,headers=headers);self.assertEqual(response.status_code,200,response.text[:500]);descriptor=response.json()
                    self.assertEqual(set(descriptor),set(v.DESCRIPTOR));self.assertEqual(len(descriptor),13);self.assertEqual(descriptor['contract'],'r_heatmap_view_v0.1');self.assertEqual(descriptor['artifacts'],registration['artifacts']);self.assertLessEqual(len(response.content),32768)
                    for event in ('classification_Owner','parent_Owner','design_Owner','explicit_design_request'):self.assertLess(events.index(event),events.index('cache_metadata'))
                    scenarios.append('all_original_Owner_reads_and_explicit_design_request_before_cache_exact13')
                    for name in v.NAMES:
                        response=await client.get(path+'/artifacts/'+name,headers=headers);self.assertEqual(response.status_code,200,response.text[:300]);self.assertEqual(response.content,bodies[name])
                        self.assertEqual(response.headers['content-type'],v.MEDIA[name]);self.assertEqual(response.headers['content-length'],str(len(bodies[name])));self.assertEqual(response.headers['x-cyomics-file-sha256'],saved.c.sha(bodies[name]))
                        self.assertEqual(response.headers['content-disposition'],('inline' if name=='heatmap.png' else 'attachment')+'; filename="'+name+'"')
                        for k,val in [('cache-control','no-store'),('x-content-type-options','nosniff'),('referrer-policy','no-referrer')]:self.assertEqual(response.headers[k],val)
                    scenarios.append('all8_bytes_MIME_headers_PNG_inline_SVG_attachment_over1MiB')
                    for request_headers,url in (({k:x for k,x in headers.items() if k!='authorization'},path),(dict(headers,authorization='Bearer '+token(OTHER)),path),(headers,path.replace(P,OTHER,1))):
                        events.clear();response=await client.get(url,headers=request_headers);self.assertIn(response.status_code,(401,404));self.assertFalse(any(e.startswith('cache_') for e in events));self.assertLessEqual(len(response.content),4096)
                    for flag,status in [('design_denied',404),('wrong_version',404),('wrong_group',503)]:
                        state[flag]=True;events.clear();response=await client.get(path,headers=headers);self.assertEqual(response.status_code,status,response.text);self.assertFalse(any(e.startswith('cache_') for e in events));state.clear()
                    scenarios.append('anonymous_foreign_context_design_denial_version_group_fail_before_cache')
                    folder.rename(root/'hidden');response=await client.get(path,headers=headers);self.assertEqual((response.status_code,response.json()),(404,{'error':{'code':'not_found'}}));(root/'hidden').rename(folder)
                    for key,val in [('derived_metadata_sha256','0'*64),('created_by',OTHER),('render_script',{'bytes':11357,'sha256':'0'*64})]:
                        bad=copy.deepcopy(registration);bad[key]=val;reg(bad);response=await client.get(path,headers=headers);self.assertEqual(response.status_code,503)
                    reg(registration);scenarios.append('missing_cache_404_and_stale_context_script_Owner_503')
                    for total in (False,True):
                        bad=copy.deepcopy(registration)
                        for p in (bad['artifacts'] if total else bad['artifacts'][:1]):p['bytes']=v.MAX_FILE if total else v.MAX_FILE+1
                        reg(bad);response=await client.get(path,headers=headers);self.assertEqual(response.status_code,503)
                    reg(registration);scenarios.append('strict_single_and_12_body_aggregate_caps')
                    # A self-consistent forged cache hash cannot bypass original pure output validation.
                    zlines=bodies['heatmap-zscore.tsv'].decode().splitlines();parts=zlines[1].split('\t');parts[2]='0';zlines[1]='\t'.join(parts);bad_body=('\n'.join(zlines)+'\n').encode()
                    self.assertNotEqual(bad_body,bodies['heatmap-zscore.tsv']);put('heatmap-zscore.tsv',bad_body);bad=copy.deepcopy(registration)
                    next(p for p in bad['artifacts'] if p['name']=='heatmap-zscore.tsv').update(v.pin(bad_body));reg(bad)
                    response=await client.get(path+'/artifacts/heatmap.png',headers=headers);self.assertEqual(response.status_code,503);put('heatmap-zscore.tsv',bodies['heatmap-zscore.tsv']);reg(registration)
                    scenarios.append('all12_hashes_and_reviewed_native_output_validator_enforced')
                    f=folder/'samples.tsv';f.unlink();outside=root/'outside';outside.write_bytes(inputs['samples.tsv']);f.symlink_to(outside)
                    response=await client.get(path,headers=headers);self.assertEqual(response.status_code,503);put('samples.tsv',inputs['samples.tsv']);(folder/'metadata.json').chmod(0o600)
                    response=await client.get(path,headers=headers);self.assertEqual(response.status_code,503);(folder/'metadata.json').chmod(0o444)
                    scenarios.append('input_symlink_mutable_metadata_refused')
                    for url in (path+'?url=https://example.invalid/x',path+'/artifacts/provenance.json',path+'/artifacts/heatmap.pdf'):
                        response=await client.get(url,headers=headers);self.assertIn(response.status_code,(400,404))
                    response=await client.post(path,headers=headers);self.assertEqual(response.status_code,405)
                    state['timeout']=True;events.clear();response=await client.get(path,headers=headers);self.assertEqual((response.status_code,response.json()),(504,{'error':{'code':'r_heatmap_read_timeout'}}));self.assertFalse(any(e.startswith('cache_') for e in events));state.clear()
                    scenarios.append('fixed_routes_private_inputs_hidden_POST_and_timeout_fail_closed')
                self.assertEqual(network.call_count,0);self.assertEqual(native.call_count,0);self.assertEqual(writer.call_count,0);self.assertEqual(design_writer.call_count,0)
            print(json.dumps({'single_finite_combination':'PASS','scenario_groups':scenarios,'real_DB_Auth_HTTP_R_cache_operations':0,'old_suites':0,'fixture':'synthetic input/output/cache only; no actual image or native proof'}))
        finally:
            for application in apps:application.state.complete_imports.close();await application.state.authority.close()
            folder.chmod(0o700);root.chmod(0o700);temp.cleanup()

if __name__=='__main__':unittest.main()
