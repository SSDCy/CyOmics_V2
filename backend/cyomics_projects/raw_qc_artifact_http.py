"""Unmounted default-off ASGI artifact endpoint; one lifetime reader/lease.

No factory, app registration, SQL, filesystem or thread activity on import/init.
Server dependencies must retain this instance for the ASGI process lifetime.
"""
import asyncio
from dataclasses import dataclass
import hashlib
import inspect
import math
import re
import threading
import time
from starlette.requests import Request
from starlette.responses import JSONResponse
from .identity import jwt_payload
from .import_http import headers,import_budget,send_error_without_wait,Gone,SECURITY
from .raw_qc_http import UUID,validate_headers,response_value
from .raw_qc_reader import ArtifactReadFailure,VerifiedArtifact
from .settings import Fault
from .validation import canonical_uuid
from . import raw_qc_contract as c

PER_FILE=67108864
PROCESS_SECONDS=9.0
CLEANUP_SECONDS=0.05
UNAVAILABLE='raw_qc_unavailable'
INTEGRITY='raw_qc_artifact_integrity_failed'
READER_UNAVAILABLE='raw_qc_artifact_unavailable'
READER_TIMEOUT='raw_qc_artifact_timeout'
CLOSE_UNKNOWN='raw_qc_artifact_cleanup_unconfirmed'
TIMEOUT='raw_qc_timeout'
ROUTE=re.compile(rb'/api/v1/raw-qc-jobs/('+UUID.encode()+rb')/artifacts/([A-Za-z0-9.-]+)')
ERRORS={400:{'invalid_raw_qc_request'},401:{'unauthenticated'},403:{'auth_origin_rejected'},
    404:{'not_found','raw_qc_job_not_found','dataset_not_found','raw_qc_artifact_not_found'},405:{'method_not_allowed'},
    409:{'raw_qc_result_not_ready'},415:{'unsupported_media_type'},
    503:{UNAVAILABLE,INTEGRITY,'raw_qc_persisted_invalid'},504:{TIMEOUT}}


def route_input(scope):
    if scope.get('type')!='http':raise Fault(404,'not_found')
    raw=scope.get('raw_path');match=ROUTE.fullmatch(raw) if type(raw) is bytes else None
    if not match or scope.get('path')!=raw.decode('ascii') or scope.get('root_path',''):
        raise Fault(404,'not_found')
    if scope.get('method')!='GET':raise Fault(405,'method_not_allowed')
    name=match[2].decode('ascii')
    if name not in c.OUTPUTS:raise Fault(404,'raw_qc_artifact_not_found')
    query=scope.get('query_string')
    if type(query) is not bytes or not re.fullmatch(rb'project_id='+UUID.encode(),query):
        raise Fault(400,'invalid_raw_qc_request')
    project=query[len(b'project_id='):].decode('ascii')
    if not canonical_uuid(project):raise Fault(400,'invalid_raw_qc_request')
    return {'method':'GET','project':project,'job':match[1].decode(),'dataset':None,'request':None,'name':name}


def artifact_headers(scope):
    validate_headers(scope,'GET')
    forbidden={'range','content-range','accept-ranges','if','if-range','if-match','if-none-match','if-modified-since','if-unmodified-since',
        'content-type','location','forwarded','nonce',
        'x-forwarded-for','x-forwarded-host','x-forwarded-proto','x-original-url','x-rewrite-url'}
    budget_names={'x-cyomics-remaining-ms','x-cyomics-deadline-unix-ms'}
    encodings=headers(scope,'accept-encoding')
    if len(encodings)>1 or encodings and encodings!=['identity']:raise Fault(400,'invalid_raw_qc_request')
    lengths=headers(scope,'content-length')
    if lengths and lengths!=['0']:raise Fault(400,'invalid_raw_qc_request')
    for key,_ in scope.get('headers',[]):
        name=key.decode('latin1').lower()
        if name in forbidden or name.startswith('if-') or 'nonce' in name or name.startswith('x-cyomics-') and name not in budget_names:
            raise Fault(400,'invalid_raw_qc_request')


def _consume(task):
    try:
        if not task.cancelled():task.exception()
    except BaseException:pass


class _Lease:
    def __init__(self):
        self.tasks=set();self.stop=threading.Event();self.read=None;self.uncertain=False
        self.rejected_futures=[]
    def track(self,task):
        try:self.tasks.add(task);task.add_done_callback(_consume)
        except BaseException:
            self.uncertain=True;task.cancel();task.add_done_callback(_consume);raise
        return task
    def settled(self):
        return (not self.uncertain and all(t.done() for t in self.tasks)
            and (self.read is None or not self.read.thread.is_alive() and self.read.cleanup_confirmed))
    def consume_rejected_done(self):
        for future in self.rejected_futures:
            if future.done():_consume(future)


def _reject_future(future,lease):
    # Protocol violation already quarantines this lease. Retain its one returned
    # Future so a closed/non-running foreign loop cannot orphan private errors.
    # No cross-loop cancellation, polling, new task/thread or capacity release.
    lease.rejected_futures.append(future)
    if future.done():_consume(future);return
    def consume_when_ready():
        if future.done():_consume(future)
        else:future.add_done_callback(_consume)
    try:
        owner=future.get_loop()
        if not owner.is_closed():owner.call_soon_threadsafe(consume_when_ready)
    except BaseException:
        if future.done():_consume(future)


class _BudgetView:
    """Delegate policy to existing Budget, tracking its dispatched child tasks."""
    def __init__(self,base,lease):self.base=base;self.lease=lease
    @property
    def deadline(self):return self.base.deadline
    @deadline.setter
    def deadline(self,value):self.base.deadline=value
    def check(self):
        self.base.check()
        if self.lease.stop.is_set():raise TimeoutError()
    def remaining(self):return self.base.remaining()
    async def run(self,operation,cap=None,abort=None,discard=None):
        async def observed():
            self.lease.track(asyncio.current_task())
            self.check()
            return await operation()
        return await self.base.run(observed,cap,abort,discard)


@dataclass(frozen=True,slots=True)
class _Artifact:
    body:bytes
    name:str
    media_type:str
    sha256:str
    length:int


class _Read:
    def __init__(self,reader,job,name,pin,lease,deadline,loop):
        self.reader=reader;self.job=job;self.name=name;self.pin=pin;self.lease=lease
        self.deadline=deadline;self.loop=loop;self.signal=loop.create_future()
        self.value=None;self.failure=None;self.cleanup_confirmed=False
        self.thread=threading.Thread(target=self.run,name='raw-qc-artifact-read',daemon=True)
    def check(self):
        if self.lease.stop.is_set() or time.monotonic()>=self.deadline:raise TimeoutError()
    def notify(self):
        if not self.signal.done():self.signal.set_result(None)
    def run(self):
        try:
            self.check()
            try:value=self.reader.read_verified(self.job,self.name,check=self.check)
            except BaseException as error:
                code=getattr(error,'code',None)
                if isinstance(error,ArtifactReadFailure) and code in ('raw_qc_artifact_reader_unconfigured',READER_UNAVAILABLE,READER_TIMEOUT):
                    self.cleanup_confirmed=True
                    self.failure=TIMEOUT if code==READER_TIMEOUT else UNAVAILABLE if code=='raw_qc_artifact_reader_unconfigured' else INTEGRITY
                else:
                    self.lease.uncertain=True;self.failure=INTEGRITY
                return
            # The reader's successful return contract includes confirmed closure.
            self.cleanup_confirmed=True
            if type(value) is not VerifiedArtifact:
                self.lease.uncertain=True
                if inspect.iscoroutine(value):value.close()
                elif isinstance(value,asyncio.Future):_reject_future(value,self.lease)
                raise ValueError()
            self.check()
            body=value.body;pin=self.pin
            if (type(body) is not bytes or type(value.length) is not int
                or not 1<=len(body)<=PER_FILE or len(body)!=value.length or value.length!=pin['bytes']
                or value.name!=self.name or value.media_type!=pin['media_type']
                or value.sha256!=pin['sha256'] or hashlib.sha256(body).hexdigest()!=pin['sha256']):
                raise ValueError()
            self.check();self.value=_Artifact(body,self.name,pin['media_type'],pin['sha256'],len(body))
        except TimeoutError:
            # A pre-call timeout owns no descriptors; a post-return timeout has
            # already established closure. A reader-call error is handled above.
            self.cleanup_confirmed=True;self.failure=TIMEOUT
        except BaseException:
            self.failure=INTEGRITY
        finally:
            try:self.loop.call_soon_threadsafe(self.notify)
            except BaseException:pass


class RawQCArtifactHTTP:
    def __init__(self,authority,repository=None,reader=None):
        self.authority=authority;self.repository=repository;self.reader=reader
        self._lease=None;self._loop=None;self._admission=threading.Lock()

    @staticmethod
    def immediate_error(send,fault):
        status,code=getattr(fault,'status',None),getattr(fault,'code',None)
        if code not in ERRORS.get(status,set()):status,code=503,UNAVAILABLE
        response=JSONResponse({'error':{'code':code}},status_code=status,headers=SECURITY)
        for message in ({'type':'http.response.start','status':status,'headers':response.raw_headers},
            {'type':'http.response.body','body':response.body,'more_body':False}):
            if not send_error_without_wait(send,message):return False
        return True

    async def __call__(self,scope,receive,send):
        # No dependency method, receive or thread is reached while default-off.
        if self.repository is None or self.reader is None or self.authority is None:
            self.immediate_error(send,Fault(503,UNAVAILABLE));return
        if self._lease is not None:
            self._lease.consume_rejected_done()
            self.immediate_error(send,Fault(503,UNAVAILABLE));return
        try:
            route=route_input(scope);artifact_headers(scope)
            base=import_budget(scope,'GET');base.deadline=min(base.deadline,time.monotonic()+PROCESS_SECONDS)
            base.check()
        except TimeoutError:
            self.immediate_error(send,Fault(504,TIMEOUT));return
        except Fault as error:
            status=error.status if error.code in ERRORS.get(error.status,set()) else 504 if error.status==504 else 400 if error.status==400 else 503
            code=error.code if error.code in ERRORS.get(error.status,set()) else TIMEOUT if status==504 else 'invalid_raw_qc_request' if status==400 else UNAVAILABLE
            self.immediate_error(send,Fault(status,code));return
        except BaseException:
            self.immediate_error(send,Fault(503,UNAVAILABLE));return
        loop=asyncio.get_running_loop()
        if not self._admission.acquire(blocking=False):
            self.immediate_error(send,Fault(503,UNAVAILABLE));return
        if self._loop is not None and self._loop is not loop:
            self._admission.release()
            self.immediate_error(send,Fault(503,UNAVAILABLE));return
        try:lease=_Lease()
        except BaseException:
            self._admission.release();self.immediate_error(send,Fault(503,UNAVAILABLE));return
        self._loop=loop;self._lease=lease;budget=_BudgetView(base,lease)
        ready=loop.create_future();start_attempted=False

        async def pump():
            while True:
                message=await receive();budget.check()
                if lease.stop.is_set() or message.get('type')=='http.disconnect':raise Gone()
                if (message.get('type')!='http.request' or ready.done()
                    or type(message.get('body',b'')) is not bytes or message.get('body',b'')
                    or message.get('more_body',False) is not False):raise Fault(400,'invalid_raw_qc_request')
                if not message.get('more_body',False):ready.set_result(None)

        async def work():
            request=Request(scope);subject=await self.authority.verify(request,budget);budget.check()
            if not canonical_uuid(subject):raise Fault(401,'unauthenticated')
            try:
                tokens=request.headers.getlist('authorization')
                if len(tokens)!=1 or not tokens[0].startswith('Bearer '):raise ValueError()
                left=jwt_payload(tokens[0][7:])['exp']-time.time()
                if not math.isfinite(left) or left<=0:raise ValueError()
            except BaseException:raise Fault(401,'unauthenticated') from None
            budget.deadline=min(budget.deadline,time.monotonic()+left)
            await budget.run(lambda:ready);budget.check()
            value=await budget.run(lambda:self.repository.read_job(subject,route['project'],route['job'],budget))
            budget.check()
            try:
                job=c.strict_document(c.canonical(value).encode('utf-8'),c.MAX_RESPONSE)
                response_value(job,route)
                if job['status']!='succeeded' or job['observation']!='confirmed':raise Fault(409,'raw_qc_result_not_ready')
                pin=next(p for p in job['result']['artifacts'] if p['name']==route['name'])
            except Fault:raise
            except BaseException:raise Fault(503,UNAVAILABLE) from None
            budget.check()
            call=_Read(self.reader,job,route['name'],pin,lease,budget.deadline,loop);lease.read=call
            try:call.thread.start()
            except BaseException:
                lease.uncertain=True;raise Fault(503,UNAVAILABLE) from None
            await budget.run(lambda:call.signal)
            # Signalling precedes the thread's return; never assume signal alone
            # proves thread termination. Wait cooperatively within the same budget.
            while call.thread.is_alive():budget.check();await asyncio.sleep(0)
            budget.check()
            if call.failure:raise Fault(504 if call.failure==TIMEOUT else 503,call.failure)
            if call.value is None:raise Fault(503,UNAVAILABLE)
            return call.value

        watcher=worker=None
        async def ensure_watcher():
            if watcher is not None and watcher.done():await watcher
        async def transmit(status,raw_headers,body):
            nonlocal start_attempted
            start_attempted=True
            try:
                await budget.run(lambda:send({'type':'http.response.start','status':status,'headers':raw_headers}))
                if status==200:await ensure_watcher()
                await budget.run(lambda:send({'type':'http.response.body','body':body,'more_body':False}))
            except BaseException:
                # A failed/aborted transport may already own a partial response.
                # Keep the admission closed even if its coroutine has returned.
                lease.uncertain=True;raise
        try:
            watcher=lease.track(asyncio.create_task(pump()));worker=lease.track(asyncio.create_task(work()))
            done,_=await budget.run(lambda:asyncio.wait({watcher,worker},return_when=asyncio.FIRST_COMPLETED))
            if watcher in done:await watcher
            artifact=await worker;budget.check();await ensure_watcher()
            h={**SECURITY,'Content-Type':'application/octet-stream','Content-Length':str(artifact.length),
                'Content-Disposition':'attachment; filename="'+artifact.name+'"','X-Cyomics-File-Sha256':artifact.sha256}
            await transmit(200,[(k.lower().encode('ascii'),v.encode('ascii')) for k,v in h.items()],artifact.body)
        except Gone:pass
        except asyncio.CancelledError:raise
        except BaseException as error:
            if not start_attempted:
                status,code=getattr(error,'status',None),getattr(error,'code',None)
                if isinstance(error,TimeoutError):status,code=504,TIMEOUT
                if code not in ERRORS.get(status,set()):status,code=503,UNAVAILABLE
                response=JSONResponse({'error':{'code':code}},status_code=status,headers=SECURITY)
                if budget.remaining()<=0:
                    start_attempted=True
                    if not self.immediate_error(send,Fault(status,code)):lease.uncertain=True
                else:
                    try:await transmit(status,response.raw_headers,response.body)
                    except asyncio.CancelledError:raise
                    except BaseException:pass
        finally:
            lease.stop.set()
            lease.consume_rejected_done()
            for task in tuple(lease.tasks):
                if not task.done():task.cancel()
            end=time.monotonic()+min(CLEANUP_SECONDS,budget.remaining())
            # Cancellation acknowledgement, not forceful thread/task shutdown.
            await asyncio.sleep(0)
            while not lease.settled() and not lease.uncertain and time.monotonic()<end:
                await asyncio.sleep(0)
            if lease.settled():self._lease=None;self._admission.release()
            else:lease.uncertain=True # latched; no completion callback releases it
