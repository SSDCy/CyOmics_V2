"""Synthetic offline ASGI lifecycle/lease tests; no files, network or real Auth."""
import asyncio,copy,gc,hashlib,json,os,sys,threading,time,unittest
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

HERE=Path(__file__).resolve().parents[1]
SUPPORT=Path(os.environ.get('CYOMICS_ARTIFACT_HTTP_SUPPORT',HERE))
sys.path.insert(0,str(SUPPORT/'tests'))
import test_raw_qc_http as f # Existing SyntheticAuthority/psycopg NeverConnect seam.
import cyomics_projects
cyomics_projects.__path__.insert(0,str(HERE/'cyomics_projects'))
from cyomics_projects import raw_qc_artifact_http as api
from cyomics_projects.raw_qc_contract import canonical,decode_record,digest
BODY=b'SYNTHETIC QC artifact\n'

def succeeded():
    row=f.fixture['record']();row[12]='running';row[15]=f.fixture['STAMP'];row[17]=uuid4()
    running=decode_record(row);result=f.fixture['result_for'](running)
    for artifact in result['artifacts']:artifact.update(bytes=len(BODY),sha256=hashlib.sha256(BODY).hexdigest())
    result['manifest_sha256']=digest(canonical(result['artifacts']))
    row[12]='succeeded';row[16]=f.fixture['STAMP']+timedelta(seconds=10)
    row[20]=canonical(result);row[21]=digest(row[20])
    return decode_record(row)

Verified=api.VerifiedArtifact
ReadFailure=api.ArtifactReadFailure

class Reader:
    def __init__(self):self.calls=[];self.error=None;self.change=None;self.entered=threading.Event();self.gate=None
    def read_verified(self,job,name,*,check):
        self.calls.append((job['job_id'],name,threading.current_thread() is threading.main_thread()))
        self.entered.set()
        if self.gate is not None:self.gate.wait() # Deliberately uncooperative schedule.
        check()
        if self.error:raise self.error
        pin=next(p for p in job['result']['artifacts'] if p['name']==name)
        value=Verified(BODY,name,pin['media_type'],pin['sha256'],len(BODY))
        return self.change(value) if self.change else value

class Repository:
    def __init__(self):self.calls=[];self.value=succeeded();self.error=None
    async def read_job(self,subject,project,job,budget):
        self.calls.append((subject,project,job))
        if subject!=f.OWNER:raise f.Fault(404,'raw_qc_job_not_found')
        if self.error:raise self.error
        return copy.deepcopy(self.value)

def scope(repo,name='raw-matrix.tsv',ms=None):
    path='/api/v1/raw-qc-jobs/'+repo.value['job_id']+'/artifacts/'+name
    h=[(b'host',b'127.0.0.1:57430'),(b'authorization',('Bearer '+f.TOKEN).encode())]
    if ms is not None:h.extend([(b'x-cyomics-remaining-ms',str(ms).encode()),(b'x-cyomics-deadline-unix-ms',str(int(time.time()*1000)+ms).encode())])
    return {'type':'http','method':'GET','path':path,'raw_path':path.encode(),'root_path':'','query_string':('project_id='+f.PROJECT).encode(),'headers':h}

async def invoke(handler,s,body=b'',q=None,send_hook=None):
    if q is None:q=asyncio.Queue();q.put_nowait({'type':'http.request','body':body,'more_body':False})
    output=[]
    async def send(message):
        output.append(message)
        if send_hook:await send_hook(message)
    await handler(s,q.get,send)
    return output

async def until(predicate):
    end=time.monotonic()+2
    while not predicate():
        if time.monotonic()>end:raise AssertionError('synthetic schedule not reached')
        await asyncio.sleep(.001)

def status(output):return output[0]['status']
def code(output):return json.loads(output[-1]['body'])['error']['code']

class Tests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.auth=f.SyntheticAuthority();self.repo=Repository();self.reader=Reader()
        self.handler=api.RawQCArtifactHTTP(self.auth,self.repo,self.reader)

    async def test_default_none_dependencies_zero_auth_repo_thread_or_receive(self):
        for h in (api.RawQCArtifactHTTP(self.auth),api.RawQCArtifactHTTP(self.auth,self.repo),api.RawQCArtifactHTTP(None,self.repo,self.reader)):
            with patch.object(threading.Thread,'start',side_effect=AssertionError('no thread')):
                out=await invoke(h,scope(self.repo))
            self.assertEqual((status(out),code(out)),(503,'raw_qc_unavailable'))
        self.assertEqual(self.auth.calls,0);self.assertEqual(self.repo.calls,[]);self.assertEqual(self.reader.calls,[])

    async def test_success_seven_fixed_names_octetstream_headers_and_single_lifetime_reader(self):
        for name in sorted(api.c.OUTPUTS):
            out=await invoke(self.handler,scope(self.repo,name));self.assertEqual(status(out),200)
            self.assertEqual(len(out),2);self.assertEqual(out[1]['body'],BODY);self.assertIs(out[1]['more_body'],False)
            h=dict(out[0]['headers'])
            self.assertEqual(h[b'content-type'],b'application/octet-stream')
            self.assertEqual(h[b'content-length'],str(len(BODY)).encode())
            self.assertEqual(h[b'content-disposition'],('attachment; filename="'+name+'"').encode())
            self.assertEqual(h[b'x-cyomics-file-sha256'],hashlib.sha256(BODY).hexdigest().encode())
            self.assertEqual(h[b'cache-control'],b'no-store');self.assertEqual(h[b'x-content-type-options'],b'nosniff');self.assertEqual(h[b'referrer-policy'],b'no-referrer')
            self.assertNotIn(b'content-encoding',h);self.assertNotIn(b'location',h);self.assertIsNone(self.handler._lease)
        self.assertEqual(self.auth.calls,7);self.assertEqual(len(self.reader.calls),7)
        self.assertTrue(all(not call[2] for call in self.reader.calls))
        self.assertTrue(all(call[0]==f.OWNER for call in self.repo.calls));self.assertIs(self.handler.reader,self.reader)

    async def test_missing_authorization_401_and_foreign_owner_404_have_zero_reader_calls(self):
        s=scope(self.repo);s['headers']=s['headers'][:1]
        self.assertEqual(status(await invoke(self.handler,s)),401);self.assertEqual(self.repo.calls,[])
        other=api.RawQCArtifactHTTP(f.SyntheticAuthority(f.OTHER),self.repo,self.reader)
        self.assertEqual(status(await invoke(other,scope(self.repo))),404);self.assertEqual(self.reader.calls,[])
        self.assertIsNone(other._lease)

    async def test_duplicate_authorization_refused_before_repository(self):
        s=scope(self.repo);s['headers'].append(s['headers'][1])
        self.assertEqual(status(await invoke(self.handler,s)),401)
        self.assertEqual(self.repo.calls,[]);self.assertEqual(self.reader.calls,[])

    async def test_route_query_method_and_encoding_variants_rejected_before_auth(self):
        changes=[lambda s:s.update(raw_path=s['raw_path']+b'/'),lambda s:s.update(root_path='/proxy'),
            lambda s:s.update(path=s['path'].upper()),lambda s:s.update(raw_path=s['raw_path'].replace(b'raw-matrix',b'%72aw-matrix')),
            lambda s:s.update(query_string=s['query_string']+b'&project_id='+f.PROJECT.encode()),
            lambda s:s.update(query_string=s['query_string']+b'&owner_id=x'),lambda s:s.update(query_string=s['query_string'].replace(b'project_id',b'%70roject_id')),
            lambda s:s.update(method='HEAD'),lambda s:s.update(method='POST')]
        for change in changes:
            s=scope(self.repo);change(s);self.assertIn(status(await invoke(self.handler,s)),(400,404,405))
        self.assertEqual(status(await invoke(self.handler,scope(self.repo,'private.json'))),404)
        self.assertEqual(self.auth.calls,0);self.assertEqual(self.repo.calls,[]);self.assertEqual(self.reader.calls,[])

    async def test_range_conditions_compression_nonce_cookie_owner_and_proxy_headers_rejected(self):
        for k,v in [(b'range',b'bytes=0-1'),(b'if-none-match',b'*'),(b'if-range',b'x'),(b'if-modified-since',b'x'),
            (b'accept-encoding',b'gzip'),(b'content-encoding',b'gzip'),(b'nonce',b'x'),(b'x-cyomics-request-nonce',b'x'),
            (b'cookie',b'x=y'),(b'x-owner-id',f.OWNER.encode()),(b'forwarded',b'host=other'),(b'location',b'https://other')]:
            s=scope(self.repo);s['headers'].append((k,v));self.assertIn(status(await invoke(self.handler,s)),(400,403,415))
        self.assertEqual(self.auth.calls,0);self.assertEqual(self.reader.calls,[])

    async def test_host_origin_and_duplicate_headers_fail(self):
        for k,v in [(b'host',b'127.0.0.1:4206'),(b'origin',b'https://other'),(b'sec-fetch-site',b'cross-site')]:
            s=scope(self.repo);s['headers']=[x for x in s['headers'] if x[0]!=k]+[(k,v)]
            self.assertEqual(status(await invoke(self.handler,s)),403)
        s=scope(self.repo);s['headers']+=s['headers'][:1];self.assertEqual(status(await invoke(self.handler,s)),403)
        self.assertEqual(self.auth.calls,0)

    async def test_GET_body_and_more_body_refused_no_repo_or_file_read(self):
        out=await invoke(self.handler,scope(self.repo),b'x')
        self.assertEqual((status(out),code(out)),(400,'invalid_raw_qc_request'));self.assertIsNone(self.handler._lease)
        q=asyncio.Queue();q.put_nowait({'type':'http.request','body':b'','more_body':True})
        out=await invoke(self.handler,scope(self.repo),q=q)
        self.assertEqual((status(out),code(out)),(400,'invalid_raw_qc_request'));self.assertIsNone(self.handler._lease)
        self.assertEqual(self.repo.calls,[]);self.assertEqual(self.reader.calls,[])

    async def test_expired_bridge_budget_no_auth_or_thread(self):
        s=scope(self.repo,ms=1);s['headers'][-1]=(b'x-cyomics-deadline-unix-ms',str(int(time.time()*1000)-1).encode())
        self.assertEqual((status(await invoke(self.handler,s))),504);self.assertEqual(self.auth.calls,0);self.assertEqual(self.reader.calls,[])

    async def test_budget_expiry_between_construction_and_admission_maps504(self):
        with patch.object(api,'import_budget',return_value=f.fixture['Budget'](0)):
            out=await invoke(self.handler,scope(self.repo))
        self.assertEqual((status(out),code(out)),(504,'raw_qc_timeout'));self.assertEqual(self.auth.calls,0)

    async def test_expired_JWT_after_authority_success_is_401_without_repo(self):
        with patch.object(api,'jwt_payload',return_value={'exp':time.time()-1}):out=await invoke(self.handler,scope(self.repo))
        self.assertEqual(status(out),401);self.assertEqual(self.repo.calls,[]);self.assertEqual(self.reader.calls,[])

    async def test_identity_encoding_zero_length_and_valid_origin_are_permitted(self):
        s=scope(self.repo);s['headers'] += [(b'origin',b'http://127.0.0.1:4206'),(b'accept-encoding',b'identity'),(b'content-encoding',b'identity'),(b'content-length',b'0')]
        self.assertEqual(status(await invoke(self.handler,s)),200)
        bad=scope(self.repo);bad['headers'].append((b'content-length',b'00'))
        self.assertEqual(status(await invoke(self.handler,bad)),400)

    async def test_admission_gate_cannot_create_an_additional_lease(self):
        self.handler._admission.acquire()
        try:self.assertEqual(status(await invoke(self.handler,scope(self.repo))),503)
        finally:self.handler._admission.release()
        self.assertEqual(self.auth.calls,0);self.assertEqual(self.reader.calls,[])
        self.assertEqual(status(await invoke(self.handler,scope(self.repo))),200)

    async def test_running_job_not_ready_and_corrupt_manifest_do_not_read(self):
        saved=copy.deepcopy(self.repo.value)
        self.repo.value=f.PUBLIC
        out=await invoke(self.handler,scope(self.repo));self.assertEqual((status(out),code(out)),(409,'raw_qc_result_not_ready'))
        self.repo.value=saved;self.repo.value['result']['artifacts'][0]['sha256']='0'*64
        out=await invoke(self.handler,scope(self.repo));self.assertEqual((status(out),code(out)),(503,'raw_qc_persisted_invalid'))
        self.assertEqual(self.reader.calls,[])

    async def test_manifest_64MiB_plus_one_rejected_before_reader(self):
        self.repo.value['result']['artifacts'][0]['bytes']=api.PER_FILE+1
        self.repo.value['result']['manifest_sha256']=digest(canonical(self.repo.value['result']['artifacts']))
        out=await invoke(self.handler,scope(self.repo));self.assertEqual(status(out),503);self.assertEqual(self.reader.calls,[])

    async def test_returned_hash_name_mime_length_and_oversize_never_emit200(self):
        from dataclasses import replace
        for change in (lambda v:replace(v,body=b'changed'),lambda v:replace(v,sha256='0'*64),
            lambda v:replace(v,name='summary.tsv'),lambda v:replace(v,media_type='text/html'),
            lambda v:replace(v,length=True),lambda v:replace(v,body=b'x'*(api.PER_FILE+1),length=api.PER_FILE+1)):
            self.reader.change=change
            out=await invoke(self.handler,scope(self.repo));self.assertEqual((status(out),code(out)),(503,'raw_qc_artifact_integrity_failed'))
            self.assertIsNone(self.handler._lease)

    async def test_typed_known_reader_failure_releases_after_error_and_success_works_next(self):
        self.reader.error=ReadFailure('raw_qc_artifact_unavailable')
        out=await invoke(self.handler,scope(self.repo));self.assertEqual((status(out),code(out)),(503,'raw_qc_artifact_integrity_failed'))
        self.assertIsNone(self.handler._lease);self.reader.error=None
        self.assertEqual(status(await invoke(self.handler,scope(self.repo))),200)

    async def test_cleanup_unknown_latches_lease_forever_no_replacement_reader(self):
        self.reader.error=ReadFailure('raw_qc_artifact_cleanup_unconfirmed')
        out=await invoke(self.handler,scope(self.repo));self.assertEqual((status(out),code(out)),(503,'raw_qc_artifact_integrity_failed'))
        self.assertIsNotNone(self.handler._lease);self.reader.error=None
        self.assertEqual(status(await invoke(self.handler,scope(self.repo))),503)
        self.assertEqual(self.auth.calls,1);self.assertEqual(len(self.reader.calls),1)

    async def test_generic_private_reader_exception_sanitized_and_latched(self):
        self.reader.error=RuntimeError('SYNTHETIC_PRIVATE_FD_PATH')
        out=await invoke(self.handler,scope(self.repo));self.assertEqual(code(out),'raw_qc_artifact_integrity_failed')
        self.assertNotIn(b'PRIVATE',out[-1]['body']);self.assertIsNotNone(self.handler._lease)

    async def test_async_reader_protocol_violation_is_closed_without_execution_or_warning(self):
        import warnings
        ran=[]
        async def invalid(*args,**kwargs):ran.append(True)
        self.reader.read_verified=invalid
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always');out=await invoke(self.handler,scope(self.repo));gc.collect()
        self.assertEqual((status(out),code(out)),(503,'raw_qc_artifact_integrity_failed'))
        self.assertEqual(ran,[]);self.assertFalse(any('never awaited' in str(v.message) for v in caught));self.assertIsNotNone(self.handler._lease)

    async def test_busy_during_read_is_immediate503_then_normal_whole_response_releases(self):
        gate=threading.Event();self.reader.gate=gate
        task=asyncio.create_task(invoke(self.handler,scope(self.repo)))
        try:
            await until(self.reader.entered.is_set)
            self.assertEqual(status(await invoke(self.handler,scope(self.repo))),503)
            self.assertEqual(self.auth.calls,1);gate.set()
            self.assertEqual(status(await task),200);self.assertIsNone(self.handler._lease)
        finally:gate.set();await until(lambda:not self.handler._lease or not self.handler._lease.read.thread.is_alive())

    async def test_timeout_hung_thread_does_not_admit_again_even_after_late_completion(self):
        gate=threading.Event();self.reader.gate=gate
        task=asyncio.create_task(invoke(self.handler,scope(self.repo,ms=500)))
        try:
            await until(self.reader.entered.is_set);out=await task
            self.assertEqual(status(out),504);self.assertIsNotNone(self.handler._lease)
            self.assertEqual(status(await invoke(self.handler,scope(self.repo))),503);self.assertEqual(len(self.reader.calls),1)
            gate.set();await until(lambda:not self.handler._lease.read.thread.is_alive())
            self.assertEqual(status(await invoke(self.handler,scope(self.repo))),503);self.assertEqual(self.auth.calls,1)
        finally:gate.set();await until(lambda:not self.handler._lease or not self.handler._lease.read or not self.handler._lease.read.thread.is_alive())

    async def test_disconnect_during_hung_read_sends_no_private_data_and_keeps_lease(self):
        gate=threading.Event();self.reader.gate=gate;q=asyncio.Queue();q.put_nowait({'type':'http.request','body':b''})
        task=asyncio.create_task(invoke(self.handler,scope(self.repo),q=q))
        try:
            await until(self.reader.entered.is_set);q.put_nowait({'type':'http.disconnect'})
            self.assertEqual(await task,[]);self.assertIsNotNone(self.handler._lease)
            self.assertEqual(status(await invoke(self.handler,scope(self.repo))),503)
        finally:gate.set();await until(lambda:not self.handler._lease.read.thread.is_alive())

    async def test_outer_cancel_during_hung_read_keeps_lease(self):
        gate=threading.Event();self.reader.gate=gate;task=asyncio.create_task(invoke(self.handler,scope(self.repo)))
        try:
            await until(self.reader.entered.is_set);task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
            self.assertIsNotNone(self.handler._lease);self.assertEqual(status(await invoke(self.handler,scope(self.repo))),503)
        finally:gate.set();await until(lambda:not self.handler._lease.read.thread.is_alive())

    async def test_cancel_ignoring_send_late_private_error_consumed_no_second_start_or_release(self):
        entered=asyncio.Event();gate=asyncio.Event();seen=[];loop=asyncio.get_running_loop();old=loop.get_exception_handler();loop.set_exception_handler(lambda l,c:seen.append(c))
        async def send(message):
            if message['type']=='http.response.body':
                entered.set()
                while not gate.is_set():
                    try:await gate.wait()
                    except asyncio.CancelledError:pass
                raise RuntimeError('SYNTHETIC_PRIVATE_SEND')
        task=asyncio.create_task(invoke(self.handler,scope(self.repo,ms=500),send_hook=send))
        try:
            await entered.wait();out=await task
            self.assertEqual(sum(m['type']=='http.response.start' for m in out),1)
            self.assertIsNotNone(self.handler._lease);self.assertEqual(status(await invoke(self.handler,scope(self.repo))),503)
            gate.set();await until(lambda:all(t.done() for t in self.handler._lease.tasks));gc.collect();await asyncio.sleep(0)
            self.assertEqual(seen,[]);self.assertEqual(status(await invoke(self.handler,scope(self.repo))),503)
        finally:gate.set();loop.set_exception_handler(old)

    async def test_budget_child_ignores_auth_cancellation_lease_withheld_no_repo_after_late_return(self):
        entered=asyncio.Event();gate=asyncio.Event()
        class Auth(f.SyntheticAuthority):
            async def verify(this,request,budget):
                subject=await super().verify(request,budget)
                async def inner():
                    entered.set()
                    while not gate.is_set():
                        try:await gate.wait()
                        except asyncio.CancelledError:pass
                    return subject
                return await budget.run(inner)
        h=api.RawQCArtifactHTTP(Auth(),self.repo,self.reader)
        task=asyncio.create_task(invoke(h,scope(self.repo,ms=250)))
        try:
            await entered.wait();self.assertEqual(status(await task),504);self.assertIsNotNone(h._lease)
            self.assertEqual(status(await invoke(h,scope(self.repo))),503);gate.set()
            await until(lambda:all(t.done() for t in h._lease.tasks))
            self.assertEqual(self.repo.calls,[]);self.assertEqual(self.reader.calls,[])
            self.assertEqual(status(await invoke(h,scope(self.repo))),503)
        finally:gate.set()

    async def test_send_start_exception_cannot_emit_second_start_or_reopen_admission(self):
        async def fail(message):raise RuntimeError('SYNTHETIC_PRIVATE_SEND_START')
        out=await invoke(self.handler,scope(self.repo),send_hook=fail)
        self.assertEqual(len(out),1);self.assertEqual(out[0]['type'],'http.response.start')
        self.assertIsNotNone(self.handler._lease)
        self.assertEqual(status(await invoke(self.handler,scope(self.repo))),503)
        self.assertEqual(self.auth.calls,1);self.assertEqual(len(self.reader.calls),1)

    async def test_disconnect_ignored_repository_cannot_start_late_thread(self):
        entered=asyncio.Event();gate=asyncio.Event();repo=self.repo;original=repo.read_job
        async def read(*args):
            entered.set()
            while not gate.is_set():
                try:await gate.wait()
                except asyncio.CancelledError:pass
            return await original(*args)
        repo.read_job=read;q=asyncio.Queue();q.put_nowait({'type':'http.request','body':b''})
        task=asyncio.create_task(invoke(self.handler,scope(repo),q=q))
        try:
            await entered.wait();q.put_nowait({'type':'http.disconnect'});self.assertEqual(await task,[])
            self.assertIsNotNone(self.handler._lease);gate.set()
            await until(lambda:all(t.done() for t in self.handler._lease.tasks))
            self.assertEqual(self.reader.calls,[]);self.assertEqual(status(await invoke(self.handler,scope(repo))),503)
        finally:gate.set()

    async def test_pending_foreign_closed_loop_future_is_retained_then_done_consumed_without_cross_cancel(self):
        closed=asyncio.new_event_loop();seen=[];closed.set_exception_handler(lambda loop,ctx:seen.append(ctx))
        future=closed.create_future();closed.close();self.reader.change=lambda value:future
        try:
            out=await invoke(self.handler,scope(self.repo));self.assertEqual(code(out),'raw_qc_artifact_integrity_failed')
            self.assertFalse(future.cancelled());self.assertFalse(future.done());self.assertIsNotNone(self.handler._lease)
            self.reader.change=None
            future.set_exception(RuntimeError('SYNTHETIC_PRIVATE_PENDING_FOREIGN'))
            gc.collect();self.assertEqual(seen,[])
            self.assertEqual(status(await invoke(self.handler,scope(self.repo))),503)
            self.assertFalse(future._log_traceback);self.assertEqual(seen,[])
            self.assertEqual(self.auth.calls,1);self.assertEqual(len(self.reader.calls),1)
        finally:
            if future.done() and not future.cancelled():future.exception()

    async def test_pending_own_loop_future_late_exception_is_consumed_without_admission_release(self):
        loop=asyncio.get_running_loop();future=loop.create_future();seen=[];old=loop.get_exception_handler();loop.set_exception_handler(lambda l,c:seen.append(c))
        self.reader.change=lambda value:future
        try:
            out=await invoke(self.handler,scope(self.repo));self.assertEqual(code(out),'raw_qc_artifact_integrity_failed')
            self.assertFalse(future.done());self.reader.change=None
            future.set_exception(RuntimeError('SYNTHETIC_PRIVATE_PENDING_OWN'))
            await asyncio.sleep(0);self.assertFalse(future._log_traceback);gc.collect();await asyncio.sleep(0)
            self.assertEqual(seen,[]);self.assertIsNotNone(self.handler._lease)
            self.assertEqual(status(await invoke(self.handler,scope(self.repo))),503)
            self.assertEqual(len(self.reader.calls),1)
        finally:
            if future.done() and not future.cancelled():future.exception()
            loop.set_exception_handler(old)

    async def test_completed_future_returned_after_timeout_is_still_consumed(self):
        closed=asyncio.new_event_loop();future=closed.create_future()
        future.set_exception(RuntimeError('SYNTHETIC_PRIVATE_LATE_FUTURE'));closed.close()
        entered=threading.Event();gate=threading.Event()
        def read(*args,**kwargs):entered.set();gate.wait();return future
        self.reader.read_verified=read
        try:
            task=asyncio.create_task(invoke(self.handler,scope(self.repo,ms=250)))
            await until(entered.is_set);out=await task;self.assertEqual(status(out),504)
            self.assertIsNotNone(self.handler._lease);gate.set()
            await until(lambda:not self.handler._lease.read.thread.is_alive())
            self.assertFalse(future._log_traceback)
            self.assertEqual(status(await invoke(self.handler,scope(self.repo))),503)
        finally:
            gate.set();await until(lambda:not self.handler._lease.read.thread.is_alive())
            future.exception()

    async def test_shortened_JWT_bounds_ready_wait_without_body_delivery(self):
        queue=asyncio.Queue();start=time.monotonic()
        with patch.object(api,'jwt_payload',return_value={'exp':time.time()+.12}):
            out=await invoke(self.handler,scope(self.repo,ms=900),q=queue)
        self.assertEqual(status(out),504);self.assertLess(time.monotonic()-start,.55)
        self.assertEqual(self.repo.calls,[]);self.assertEqual(self.reader.calls,[])

    async def test_shortened_JWT_bounds_cancel_resistant_repository_wait_without_thread(self):
        entered=asyncio.Event();gate=asyncio.Event()
        async def read(*args):
            entered.set()
            while not gate.is_set():
                try:await gate.wait()
                except asyncio.CancelledError:pass
            return copy.deepcopy(self.repo.value)
        self.repo.read_job=read;start=time.monotonic()
        try:
            with patch.object(api,'jwt_payload',return_value={'exp':time.time()+.12}):
                task=asyncio.create_task(invoke(self.handler,scope(self.repo,ms=900)))
                await entered.wait();out=await task
            self.assertEqual(status(out),504);self.assertLess(time.monotonic()-start,.55)
            self.assertEqual(self.reader.calls,[]);self.assertIsNotNone(self.handler._lease)
            gate.set();await until(lambda:all(t.done() for t in self.handler._lease.tasks))
            self.assertEqual(status(await invoke(self.handler,scope(self.repo))),503)
        finally:gate.set()

if __name__=='__main__':unittest.main(verbosity=2)
