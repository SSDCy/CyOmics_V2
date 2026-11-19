"""Default-off private FASTA ASGI boundary; original Authority/Owner/transaction.
No migration, ID mapping, native execution, fake verified bool or alternate pool.
"""
import asyncio,base64,hashlib,math,re,time
from urllib.parse import quote,unquote
from starlette.requests import Request
from starlette.responses import JSONResponse
from .identity import jwt_payload
from .raw_uploads import SECURITY,ClientGone
from .upload_repository import UploadOutcome
from .fasta_private_repository import FastaPrivateRepository
from . import fasta_intake_contract as model
from .settings import Fault,consume_task
from .validation import canonical_uuid

UUID=rb'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
BASE=rb'/api/v1/projects/('+UUID+rb')/fasta-sources'
ROUTES=(('save','POST',BASE),('list','GET',BASE),('receipt','GET',BASE+rb'/requests/('+UUID+rb')'),('source','GET',BASE+rb'/('+UUID+rb')'),('content','GET',BASE+rb'/('+UUID+rb')/content'))
ERRORS={400:{'invalid_fasta_request','fasta_checksum_mismatch'},401:{'unauthenticated'},403:{'auth_origin_rejected'},404:{'project_not_found','fasta_source_not_found'},405:{'method_not_allowed'},409:{'fasta_request_conflict'},413:{'fasta_too_large','fasta_quota_exceeded'},415:{'unsupported_media_type'},422:{'fasta_index_failed'},429:{'fasta_intake_busy','fasta_rate_limited'},503:{'fasta_intake_unavailable','fasta_source_integrity_failed','fasta_index_persisted_invalid','fasta_parser_release_mismatch'},504:{'fasta_intake_timeout','fasta_result_unknown'}}
MAX_JSON=65536

def owns(scope):
    path=scope.get('path','')
    return type(path) is str and re.match(r'^/api/v1/projects/[^/]+/fasta-sources(?:/|$)',re.sub('/+','/',unquote(path))) is not None

def classify(scope):
    raw=scope.get('raw_path')
    if type(raw) is not bytes or scope.get('query_string',b'') or scope.get('root_path',''):return None
    for kind,method,pattern in ROUTES:
        match=re.fullmatch(pattern,raw)
        if match and scope.get('method')==method and scope.get('path')==raw.decode('ascii'):
            return {'kind':kind,'project_id':match[1].decode(),'id':match[2].decode() if match.lastindex==2 else None,'long':kind!='list'}
    return None

def reject_route(scope):
    if scope.get('query_string',b''):raise Fault(400,'invalid_fasta_request')
    raw=scope.get('raw_path',b'')
    if type(raw) is bytes and not scope.get('root_path','') and scope.get('path')==raw.decode('ascii',errors='replace') and any(re.fullmatch(p,raw) for _,_,p in ROUTES):raise Fault(405,'method_not_allowed')
    raise Fault(404,'fasta_source_not_found')

def one(request,name,required=False):
    values=request.headers.getlist(name)
    if len(values)>1 or required and len(values)!=1:raise Fault(400,'invalid_fasta_request')
    return values[0] if values else None

def context_headers(request,method):
    def context(name):
        values=request.headers.getlist(name)
        if len(values)>1:raise Fault(403,'auth_origin_rejected')
        return values[0] if values else None
    if context('host')!='127.0.0.1:57430':raise Fault(403,'auth_origin_rejected')
    origin=context('origin')
    if origin is not None and origin!='http://127.0.0.1:4206' or method=='POST' and (origin is None or one(request,'x-cyomics-request')!='1'):raise Fault(403,'auth_origin_rejected')
    if context('sec-fetch-site') not in (None,'same-origin'):raise Fault(403,'auth_origin_rejected')
    if any(request.headers.getlist(k) for k in ('cookie','x-user-id','x-owner-id','x-cyomics-account-context')):raise Fault(403,'auth_origin_rejected')
    if one(request,'content-encoding') not in (None,'identity'):raise Fault(415,'unsupported_media_type')
    length,transfer=one(request,'content-length'),one(request,'transfer-encoding')
    if transfer not in (None,'chunked') or transfer is not None and (length is not None or method=='GET'):raise Fault(400,'invalid_fasta_request')
    if method=='GET' and length not in (None,'0'):raise Fault(400,'invalid_fasta_request')

def input_headers(request):
    if one(request,'content-type',True)!='application/octet-stream':raise Fault(415,'unsupported_media_type')
    request_id=one(request,'idempotency-key',True);encoded=one(request,'x-cyomics-filename',True);size=one(request,'x-cyomics-file-bytes',True);digest=one(request,'x-cyomics-file-sha256',True)
    try:
        if not canonical_uuid(request_id) or re.fullmatch('[A-Za-z0-9_-]{1,340}',encoded) is None:raise ValueError()
        raw=base64.b64decode(encoded+'='*(-len(encoded)%4),altchars=b'-_',validate=True)
        if base64.urlsafe_b64encode(raw).rstrip(b'=').decode()!=encoded:raise ValueError()
        name=raw.decode('utf8','strict')
        if not 1<=len(raw)<=255 or not name.lower().endswith(('.fa','.faa','.fasta')) or any(ord(ch)<32 or 127<=ord(ch)<=159 or ch in '/\\' for ch in name):raise ValueError()
        if one(request,'x-cyomics-file-format',True)!='protein_fasta' or re.fullmatch('[1-9][0-9]{0,7}',size) is None or re.fullmatch('[0-9a-f]{64}',digest) is None:raise ValueError()
        if int(size)>model.MAX_SOURCE:raise Fault(413,'fasta_too_large')
        length=one(request,'content-length')
        if length is not None and (re.fullmatch('[0-9]{1,10}',length) is None or int(length)!=int(size)):raise ValueError()
        return {'request_id':request_id,'filename':name,'bytes':int(size),'sha256':digest}
    except Fault:raise
    except (ValueError,TypeError,UnicodeError):raise Fault(400,'invalid_fasta_request') from None

def translated(error,budget=None):
    issue=None
    if isinstance(error,model.IntakeError):
        code=error.code;status=422 if code=='fasta_index_failed' else 504 if code=='fasta_intake_timeout' else 400 if code=='invalid_fasta_request' else 503
        if status==422 and error.issue is not None:issue={'code':error.issue.code,'line':error.issue.line,'category':error.issue.category}
    else:
        status,code=getattr(error,'status',None),getattr(error,'code',None)
        if code=='upload_result_unknown':status,code=504,'fasta_result_unknown'
        elif code=='uploads_unavailable':status,code=503,'fasta_intake_unavailable'
        elif code=='invalid_project_query':status,code=400,'invalid_fasta_request'
    if isinstance(error,TimeoutError) or budget is not None and budget.remaining()<=0:
        unknown=budget is not None and (budget.create.commit_started or budget.create.committed) and not budget.create.rollback_confirmed
        status,code=504,'fasta_result_unknown' if unknown else 'fasta_intake_timeout'
    if code not in ERRORS.get(status,set()):status,code=503,'fasta_intake_unavailable';issue=None
    value={'code':code}
    if issue is not None:value['issue']=issue
    return JSONResponse({'error':value},status_code=status,headers=SECURITY)

class FastaPrivateHTTP:
    owns=staticmethod(owns)
    def __init__(self,authority,pool,budget_factory,*,enabled=False):
        self.authority=authority;self.enabled=enabled;self.repository=FastaPrivateRepository(pool) if enabled else None;self.budget_factory=budget_factory
        self.long_active=0;self.subject_active=set();self.attempts={}
    async def __call__(self,scope,receive,send):
        async def error(exc,budget=None):await translated(exc,budget)(scope,receive,send)
        if self.enabled is not True:return await error(Fault(503,'fasta_intake_unavailable'))
        budget=None
        try:
            route=classify(scope)
            if route is None:reject_route(scope)
            budget=self.budget_factory(scope);budget.create=UploadOutcome();scope['cyomics.budget']=budget
        except Exception as exc:return await error(exc,budget)
        if route['long'] and self.long_active>=2:return await error(Fault(429,'fasta_intake_busy'))
        if route['long']:self.long_active+=1
        subject=None;admitted=False;sent=False;ready=asyncio.get_running_loop().create_future()
        async def pump():
            chunks=bytearray();cap=model.MAX_SOURCE if route['kind']=='save' else 0
            while True:
                budget.check();event=await receive()
                if event['type']=='http.disconnect':raise ClientGone()
                if event['type']!='http.request' or ready.done():raise Fault(400,'invalid_fasta_request')
                raw=event.get('body',b'')
                if type(raw) is not bytes:raise Fault(400,'invalid_fasta_request')
                if len(chunks)+len(raw)>cap:raise Fault(413 if cap else 400,'fasta_too_large' if cap else 'invalid_fasta_request')
                chunks.extend(raw)
                if not event.get('more_body',False):
                    ready.set_result(bytes(chunks));chunks.clear()
                    while True:
                        later=await receive()
                        if later['type']=='http.disconnect':raise ClientGone()
                        raise Fault(400,'invalid_fasta_request')
        async def work():
            nonlocal subject,admitted
            request=Request(scope);context_headers(request,scope['method']);values=input_headers(request) if route['kind']=='save' else None
            subject=await self.authority.verify(request,budget);left=jwt_payload(request.headers.getlist('authorization')[0][7:])['exp']-time.time()
            if not math.isfinite(left) or left<=0:raise Fault(401,'unauthenticated')
            budget.deadline=min(budget.deadline,time.monotonic()+left);budget.check()
            if route['kind']=='save':
                now=time.monotonic();self.attempts={k:v for k,v in self.attempts.items() if v[0]>now}
                if subject in self.subject_active:raise Fault(429,'fasta_intake_busy')
                until,count=self.attempts.get(subject,(now+600,0))
                if count>=10:raise Fault(429,'fasta_rate_limited')
                if subject not in self.attempts and len(self.attempts)>=256:raise Fault(429,'fasta_intake_busy')
                self.attempts[subject]=(until,count+1);self.subject_active.add(subject);admitted=True
                await self.repository.ensure_project(subject,route['project_id'],budget)
            raw=await ready;budget.check()
            if route['kind']=='save':
                if len(raw)!=values['bytes']:raise Fault(400,'invalid_fasta_request')
                if hashlib.sha256(raw).hexdigest()!=values['sha256']:raise Fault(400,'fasta_checksum_mismatch')
                code,saved=await self.repository.save(subject,route['project_id'],values['request_id'],values['filename'],raw,budget,config_json=model.CONFIG_JSON);value=saved.dto()
            elif route['kind']=='list':code=200;value=await self.repository.list(subject,route['project_id'],budget,config_json=model.CONFIG_JSON)
            else:
                method=self.repository.read if route['kind']=='receipt' else self.repository.read_source
                saved=await method(subject,route['project_id'],route['id'],budget,config_json=model.CONFIG_JSON);code=200;value=saved.dto()
                if route['kind']=='content':
                    source=saved.source;return code,{**SECURITY,'Content-Type':'application/octet-stream','Content-Length':str(source.bytes),'X-Cyomics-File-SHA256':source.sha256,
                        'Content-Disposition':"attachment; filename=\"source.fasta\"; filename*=UTF-8''"+quote(source.filename,safe='')},source.content
            budget.check();response=JSONResponse(value,status_code=code,headers=SECURITY)
            if len(response.body)>MAX_JSON:raise Fault(503,'fasta_intake_unavailable')
            return code,dict(response.headers),response.body
        watcher,worker=asyncio.create_task(pump()),asyncio.create_task(work())
        try:
            done,_=await budget.run(lambda:asyncio.wait({watcher,worker},return_when=asyncio.FIRST_COMPLETED))
            if watcher in done:await watcher
            code,headers,body=await worker;budget.check()
            if len(body)>(model.MAX_SOURCE if route['kind']=='content' else MAX_JSON):raise Fault(503,'fasta_intake_unavailable')
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
            if admitted:self.subject_active.discard(subject)
            if route['long']:self.long_active-=1
            await asyncio.sleep(0)
