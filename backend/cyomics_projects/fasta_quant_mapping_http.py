"""Default-off private complete FASTA/quantification mapping HTTP, no worker."""
import asyncio,json,math,re,time
from urllib.parse import unquote
from starlette.requests import Request
from starlette.responses import JSONResponse
from .identity import jwt_payload
from .raw_uploads import SECURITY,ClientGone
from .upload_repository import UploadOutcome
from .fasta_quant_mapping_repository import FastaQuantMappingRepository,inputs,INPUT_KEYS
from .sample_http import validate_headers
from . import fasta_intake_contract as model, fasta_quant_mapping as core
from .settings import Fault,consume_task
from .validation import canonical_uuid

UUID=rb'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
BASE=rb'/api/v1/projects/('+UUID+rb')/fasta-quant-mappings'
ROUTES=(('save','POST',BASE),('lookup','GET',BASE+rb'/lookup'),('version','GET',BASE+rb'/('+UUID+rb')'),('content','GET',BASE+rb'/('+UUID+rb')/content'))
MAX_JSON=65536
ERRORS={400:{'invalid_fasta_mapping_request'},401:{'unauthenticated'},403:{'auth_origin_rejected'},404:{'project_not_found','fasta_mapping_not_found','fasta_mapping_input_not_found'},405:{'method_not_allowed'},413:{'fasta_mapping_too_large','fasta_mapping_quota_exceeded'},422:{'fasta_mapping_limit_exceeded'},429:{'fasta_mapping_busy','fasta_mapping_rate_limited'},503:{'fasta_mapping_unavailable','fasta_mapping_source_integrity_failed','fasta_mapping_persisted_invalid'},504:{'fasta_mapping_timeout','fasta_mapping_result_unknown'}}

def owns(scope):
    path=scope.get('path','')
    return type(path) is str and re.match(r'^/api/v1/projects/[^/]+/fasta-quant-mappings(?:/|$)',re.sub('/+','/',unquote(path))) is not None

def parse_inputs(raw):
    def unique(pairs):
        value={}
        for k,v in pairs:
            if k in value:raise ValueError()
            value[k]=v
        return value
    try:
        if type(raw) is not bytes or not 1<=len(raw)<=512:raise ValueError()
        return inputs(json.loads(raw.decode('utf8'),object_pairs_hook=unique,parse_constant=lambda _:(_ for _ in ()).throw(ValueError())))
    except (ValueError,UnicodeError,TypeError,RecursionError):raise Fault(400,'invalid_fasta_mapping_request') from None

def classify(scope):
    raw=scope.get('raw_path');query=scope.get('query_string',b'')
    if type(raw) is not bytes or type(query) is not bytes or scope.get('root_path',''):return None
    for kind,method,pattern in ROUTES:
        match=re.fullmatch(pattern,raw)
        if match and scope.get('method')==method and scope.get('path')==raw.decode('ascii'):
            values=None
            if kind=='lookup':
                if not 1<=len(query)<=256:return None
                values={}
                for pair in query.split(b'&'):
                    bits=pair.split(b'=')
                    if len(bits)!=2:return None
                    try:k,v=(b.decode('ascii') for b in bits)
                    except UnicodeError:return None
                    if k not in INPUT_KEYS or k in values or not canonical_uuid(v):return None
                    values[k]=v
                if set(values)!=set(INPUT_KEYS):return None
            elif query:return None
            return dict(kind=kind,project_id=match[1].decode(),id=match[2].decode() if match.lastindex==2 else None,inputs=values,long=kind in ('save','content'))
    return None

def reject_route(scope):
    raw=scope.get('raw_path',b'')
    if type(raw) is bytes and not scope.get('root_path','') and scope.get('path')==raw.decode('ascii',errors='replace') and any(re.fullmatch(p,raw) for _,_,p in ROUTES):
        if any(re.fullmatch(p,raw) and scope.get('method')==method for _,method,p in ROUTES):raise Fault(400,'invalid_fasta_mapping_request')
        raise Fault(405,'method_not_allowed')
    raise Fault(404,'fasta_mapping_not_found')

def translated(error,budget=None,*,method=None):
    status,code=getattr(error,'status',None),getattr(error,'code',None)
    if isinstance(error,model.IntakeError):status,code=(504,'fasta_mapping_timeout') if error.code=='fasta_intake_timeout' else (503,'fasta_mapping_source_integrity_failed')
    elif isinstance(error,core.MappingError):
        status,code=(422,'fasta_mapping_limit_exceeded') if str(error) in ('mapping_output_byte_limit','mapping_match_reference_limit') else (503,'fasta_mapping_source_integrity_failed')
    elif code in ('upload_result_unknown','import_result_unknown'):status,code=504,'fasta_mapping_result_unknown'
    elif code in ('fasta_intake_timeout','import_timeout'):status,code=504,'fasta_mapping_timeout'
    elif code in ('file_not_found','dataset_version_not_found','fasta_source_not_found'):status,code=404,'fasta_mapping_input_not_found'
    elif code in ('import_source_integrity_failed','import_persisted_report_invalid','parser_release_mismatch','fasta_index_persisted_invalid'):status,code=503,'fasta_mapping_source_integrity_failed'
    elif code=='invalid_project_query':status,code=400,'invalid_fasta_mapping_request'
    unknown=method=='POST' and budget is not None and (budget.create.commit_started or budget.create.committed) and not budget.create.rollback_confirmed
    if unknown:
        status,code=504,'fasta_mapping_result_unknown'
    elif isinstance(error,TimeoutError) or budget is not None and budget.remaining()<=0:
        status,code=504,'fasta_mapping_timeout'
    if code not in ERRORS.get(status,set()):status,code=503,'fasta_mapping_unavailable'
    return JSONResponse({'error':{'code':code}},status_code=status,headers=SECURITY)

class FastaQuantMappingHTTP:
    owns=staticmethod(owns)
    def __init__(self,authority,pool,budget_factory,*,enabled=False):
        self.authority=authority;self.enabled=enabled;self.repository=FastaQuantMappingRepository(pool) if enabled else None;self.budget_factory=budget_factory
        self.long_active=0;self.compute_active=0;self.subject_active=set();self.attempts={}
    async def __call__(self,scope,receive,send):
        async def error(exc,budget=None):await translated(exc,budget,method=scope.get('method'))(scope,receive,send)
        if self.enabled is not True:return await error(Fault(503,'fasta_mapping_unavailable'))
        budget=None
        try:
            route=classify(scope)
            if route is None:reject_route(scope)
            budget=self.budget_factory(scope);budget.create=UploadOutcome();scope['cyomics.budget']=budget
        except Exception as exc:return await error(exc,budget)
        if route['long'] and self.long_active>=2:return await error(Fault(429,'fasta_mapping_busy'))
        if route['long']:self.long_active+=1
        subject=None;admitted=False;sent=False;ready=asyncio.get_running_loop().create_future()
        async def pump():
            chunks=bytearray();cap=512 if route['kind']=='save' else 0
            while True:
                budget.check();event=await receive()
                if event['type']=='http.disconnect':raise ClientGone()
                if event['type']!='http.request' or ready.done():raise Fault(400,'invalid_fasta_mapping_request')
                raw=event.get('body',b'')
                if type(raw) is not bytes:raise Fault(400,'invalid_fasta_mapping_request')
                if len(chunks)+len(raw)>cap:raise Fault(413 if cap else 400,'fasta_mapping_too_large' if cap else 'invalid_fasta_mapping_request')
                chunks.extend(raw)
                if not event.get('more_body',False):
                    ready.set_result(bytes(chunks));chunks.clear()
                    while True:
                        later=await receive()
                        if later['type']=='http.disconnect':raise ClientGone()
                        raise Fault(400,'invalid_fasta_mapping_request')
        async def work():
            nonlocal subject,admitted
            request=Request(scope)
            try:declared=validate_headers(scope,scope['method'])
            except Fault as exc:
                if exc.status==403:raise
                if exc.status==413:raise Fault(413,'fasta_mapping_too_large') from None
                raise Fault(400,'invalid_fasta_mapping_request') from None
            if declared is not None and declared>512:raise Fault(413,'fasta_mapping_too_large')
            subject=await self.authority.verify(request,budget);left=jwt_payload(request.headers.getlist('authorization')[0][7:])['exp']-time.time()
            if not math.isfinite(left) or left<=0:raise Fault(401,'unauthenticated')
            budget.deadline=min(budget.deadline,time.monotonic()+left);budget.check()
            raw=await ready;budget.check()
            if declared is not None and len(raw)!=declared:raise Fault(400,'invalid_fasta_mapping_request')
            kind=route['kind'];project=route['project_id']
            if kind in ('save','lookup'):
                values=parse_inputs(raw) if kind=='save' else route['inputs']
                saved=await self.repository.lookup(subject,project,values,budget)
                if kind=='lookup':
                    if saved is None:raise Fault(404,'fasta_mapping_not_found')
                    code=200
                elif saved is not None:code=200
                else:
                    now=time.monotonic();self.attempts={k:v for k,v in self.attempts.items() if v[0]>now}
                    if self.compute_active>=1 or subject in self.subject_active:raise Fault(429,'fasta_mapping_busy')
                    until,count=self.attempts.get(subject,(now+600,0))
                    if count>=10:raise Fault(429,'fasta_mapping_rate_limited')
                    if subject not in self.attempts and len(self.attempts)>=256:raise Fault(429,'fasta_mapping_busy')
                    self.attempts[subject]=(until,count+1);self.subject_active.add(subject);self.compute_active+=1;admitted=True
                    code,saved=await self.repository.save(subject,project,values,budget)
            else:code=200;saved=await self.repository.read(subject,project,route['id'],budget)
            value=saved.metadata
            if kind=='content':
                return code,{**SECURITY,'Content-Type':'application/json','Content-Length':str(len(saved.content)),
                    'X-Cyomics-File-SHA256':value['result']['sha256'],
                    'Content-Disposition':'attachment; filename="cyomics_fasta_quant_mapping_'+value['mapping_version_id']+'.json"'},saved.content
            budget.check();response=JSONResponse(value,status_code=code,headers=SECURITY)
            if len(response.body)>MAX_JSON:raise Fault(503,'fasta_mapping_unavailable')
            return code,dict(response.headers),response.body
        watcher,worker=asyncio.create_task(pump()),asyncio.create_task(work())
        try:
            done,_=await budget.run(lambda:asyncio.wait({watcher,worker},return_when=asyncio.FIRST_COMPLETED))
            if watcher in done:await watcher
            code,headers,body=await worker;budget.check()
            if len(body)>(33554432 if route['kind']=='content' else MAX_JSON):raise Fault(503,'fasta_mapping_unavailable')
            if watcher.done():await watcher
            await budget.run(lambda:send({'type':'http.response.start','status':code,'headers':[(k.lower().encode('ascii'),v.encode('latin1')) for k,v in headers.items()]}));sent=True
            for offset in range(0,max(1,len(body)),65536):
                if watcher.done():await watcher
                part=body[offset:offset+65536]
                await budget.run(lambda:send({'type':'http.response.body','body':part,'more_body':offset+len(part)<len(body)}))
        except ClientGone:pass
        except asyncio.CancelledError:raise
        except Exception as exc:
            if not sent:await error(exc,budget)
        finally:
            for task in (worker,watcher):
                if not task.done():task.cancel()
                task.add_done_callback(consume_task)
            if admitted:self.subject_active.discard(subject);self.compute_active-=1
            if route['long']:self.long_active-=1
            await asyncio.sleep(0)
