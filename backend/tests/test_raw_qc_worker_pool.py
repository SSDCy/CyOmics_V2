"""Fully synthetic pool/credential metadata tests. No socket or real secret file."""
import asyncio,json,os,sys,types,unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from cyomics_projects import raw_qc_worker_pool as p
MODULE_SOURCE=Path(p.__file__).read_bytes()

def secret():return json.dumps({'schemaVersion':1,'purpose':'raw_qc_worker_runtime','password':'S'*64}).encode()

class Connection:
    def __init__(self):self.closed=False;self.pgconn=self;self.fail=False;self.finishes=0
    def finish(self):
        self.finishes+=1
        if self.fail:raise BaseException('SYNTHETIC_PRIVATE_DRIVER_TEXT')
        self.closed=True

class SyncTests(unittest.TestCase):
    def test_default_construct_and_close_zero_io(self):
        with patch.object(p,'_load_password',side_effect=AssertionError('read')),patch.object(p,'_connect',side_effect=AssertionError('socket')):
            pool=p.WorkerPool();pool.close()
    def test_config_exact_server_object_only(self):
        for c in ({'expected_uid':1000},'path',p.Config(True),p.Config(0),p.Config(-1)):
            with self.assertRaises(p.Fault):p.WorkerPool(c)
        with self.assertRaises(TypeError):p.Config(expected_uid=1000,host='elsewhere')
    def test_strict_secret_document_not_a_dto(self):
        self.assertEqual(p._decode_secret(secret()),'S'*64)
        for raw in (b'',b'x'*4097,b'\xff',b'\xef\xbb\xbf'+secret(),secret()[:-1]+b',"purpose":"raw_qc_worker_runtime"}',b'{"x":NaN}',b'[]'):
            with self.subTest(raw=raw[:10]),self.assertRaises(p.Fault):p._decode_secret(raw)
        for key,value in [('schemaVersion',True),('purpose','cyomicsv2_business_runtime'),('password','x'*63),('password','!'*64),('password',None),('owner_id','x')]:
            document=json.loads(secret());document[key]=value
            with self.subTest(key=key),self.assertRaises(p.Fault):p._decode_secret(json.dumps(document).encode())
    def metadata(self,mode,uid=1000,size=0,nlink=1,ino=1):
        return SimpleNamespace(st_mode=mode,st_uid=uid,st_size=size,st_nlink=nlink,st_dev=1,st_ino=ino,st_mtime_ns=2,st_ctime_ns=3)
    def loader(self,*,bad_directory=False,bad_file=None,replaced=False):
        import stat
        raw=secret();directory=self.metadata(stat.S_IFDIR|0o700)
        regular=self.metadata(stat.S_IFREG|0o600,size=len(raw))
        count=iter(range(10,30));file_fd=15
        def fstat(fd):
            if fd==file_fd:return bad_file or regular
            if bad_directory:return self.metadata(stat.S_IFDIR|0o777)
            return directory
        with ExitStack() as stack:
            for name in ('O_DIRECTORY','O_CLOEXEC','O_NOFOLLOW','O_NONBLOCK'):stack.enter_context(patch.object(p.os,name,0x4000,create=True))
            opened=stack.enter_context(patch.object(p.os,'open',side_effect=lambda *a,**kw:next(count)))
            stack.enter_context(patch.object(p.os,'fstat',side_effect=fstat))
            stack.enter_context(patch.object(p.os,'stat',return_value=self.metadata(stat.S_IFREG|0o600,size=len(raw),ino=9) if replaced else regular))
            stack.enter_context(patch.object(p.os,'read',side_effect=[raw,b'']))
            closed=stack.enter_context(patch.object(p.os,'close'))
            result=p._load_password(1000)
            self.assertEqual([call.args[0] for call in opened.call_args_list],['/','home','user01','Cy_Omics_v2.0','runtime','raw-qc-worker.json'])
            self.assertTrue(all(call.kwargs.get('dir_fd') is not None for call in opened.call_args_list[1:]))
            self.assertEqual(closed.call_count,6)
            return result
    def test_fixed_descriptor_walk_and_no_follow_flags(self):self.assertEqual(self.loader(),'S'*64)
    def test_unsafe_ancestors_file_metadata_replacement_rejected(self):
        import stat
        with self.assertRaises(p.Fault):self.loader(bad_directory=True)
        for changed in (self.metadata(stat.S_IFLNK|0o600,size=len(secret())),self.metadata(stat.S_IFREG|0o644,size=len(secret())),
                        self.metadata(stat.S_IFREG|0o600,uid=0,size=len(secret())),self.metadata(stat.S_IFREG|0o600,size=len(secret()),nlink=2),self.metadata(stat.S_IFREG|0o600,size=4097)):
            with self.assertRaises(p.Fault):self.loader(bad_file=changed)
        with self.assertRaises(p.Fault):self.loader(replaced=True)

class PoolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(p.os,'name','posix'))
        self.stack.enter_context(patch.object(p.os,'getuid',return_value=1000,create=True))
        self.stack.enter_context(patch.object(p.os,'geteuid',return_value=1000,create=True))
        self.stack.enter_context(patch.dict(p.os.environ,{},clear=True))
        self.password=self.stack.enter_context(patch.object(p,'_load_password',return_value='S'*64))
        self.driver=self.stack.enter_context(patch.object(p,'_connect',new_callable=AsyncMock,side_effect=lambda _:Connection()))
    async def test_disabled_getconn_no_secret_or_driver(self):
        with self.assertRaises(p.Fault):await p.WorkerPool().getconn()
        self.password.assert_not_called();self.driver.assert_not_called()
    async def test_PG_name_only_and_UID_refusal_pre_driver(self):
        for variant in ('env','uid'):
            with self.subTest(variant=variant):
                if variant=='env':os.environ['PGPASSWORD']='DO_NOT_READ'
                else:self.stack.enter_context(patch.object(p.os,'geteuid',return_value=999))
                with self.assertRaises(p.Fault):await p.WorkerPool(p.Config(1000)).getconn()
                os.environ.clear()
        self.password.assert_not_called();self.driver.assert_not_called()
    async def test_lazy_secret_failure_releases_unconnected_slot(self):
        pool=p.WorkerPool(p.Config(1000));self.password.side_effect=p.unavailable()
        with self.assertRaises(p.Fault):await pool.getconn()
        self.driver.assert_not_called();self.password.side_effect=None
        connection=await pool.getconn();connection.finish();pool.release(connection)
    async def test_single_capacity_no_idle_and_duplicate_release_no_overrelease(self):
        pool=p.WorkerPool(p.Config(1000));one=await pool.getconn()
        waiting=asyncio.create_task(pool.getconn());await asyncio.sleep(0)
        self.assertEqual(self.driver.await_count,1);self.assertFalse(waiting.done())
        one.finish();pool.release(one);two=await waiting
        self.assertIsNot(one,two);self.assertEqual(self.driver.await_count,2)
        with self.assertRaises(p.Fault):pool.release(one)
        pool.close();self.assertTrue(two.closed)
        with self.assertRaises(p.Fault):await pool.getconn()
    async def test_foreign_release_does_not_close_or_free_lease(self):
        pool=p.WorkerPool(p.Config(1000));current=await pool.getconn();foreign=Connection()
        with self.assertRaises(p.Fault):pool.release(foreign)
        self.assertFalse(foreign.closed);self.assertIs(pool._lease,current)
        pool.release(current);self.assertTrue(current.closed)
    async def test_BaseException_finish_failure_withholds_and_no_raw_error(self):
        pool=p.WorkerPool(p.Config(1000));connection=await pool.getconn();connection.fail=True
        with self.assertRaises(p.Fault) as cm:pool.release(connection)
        self.assertNotIn('PRIVATE',str(cm.exception));self.assertTrue(pool._held)
        pool.close();self.assertFalse(connection.closed)
        with self.assertRaises(p.Fault):await pool.getconn()
    async def test_driver_exception_or_cancel_without_handle_is_unknown(self):
        for error in (RuntimeError('SYNTHETIC_PRIVATE_DRIVER_TEXT'),asyncio.CancelledError()):
            pool=p.WorkerPool(p.Config(1000));self.driver.side_effect=error
            with self.assertRaises((p.Fault,asyncio.CancelledError)):await pool.getconn()
            self.assertTrue(pool._held);self.assertTrue(pool._uncertain)
            with self.assertRaises(p.Fault):await pool.getconn()
    async def test_cancel_waiter_does_not_release_active_capacity(self):
        pool=p.WorkerPool(p.Config(1000));one=await pool.getconn();task=asyncio.create_task(pool.getconn())
        await asyncio.sleep(0);task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertIs(pool._lease,one);self.assertTrue(pool._held)
        pool.release(one);two=await pool.getconn();pool.release(two)
    async def test_close_during_secret_load_prevents_connect(self):
        pool=p.WorkerPool(p.Config(1000))
        def load(_):pool.close();return 'S'*64
        self.password.side_effect=load
        with self.assertRaises(p.Fault):await pool.getconn()
        self.driver.assert_not_called();self.assertFalse(pool._held)
    async def test_invalid_or_already_closed_driver_return(self):
        closed=Connection();closed.finish()
        for result in (None,closed):
            pool=p.WorkerPool(p.Config(1000));self.driver.side_effect=None;self.driver.return_value=result
            with self.assertRaises(p.Fault):await pool.getconn()
            self.assertEqual(pool._held,result is None)
    async def late(self,*,cancel=False,finish_failure=False):
        pool=p.WorkerPool(p.Config(1000));connection=Connection();connection.fail=finish_failure
        entered=asyncio.Event();gate=asyncio.Event()
        async def connect(_):
            entered.set()
            try:await gate.wait()
            except asyncio.CancelledError:await gate.wait()
            return connection
        self.driver.side_effect=connect
        task=asyncio.create_task(pool.getconn());await entered.wait()
        if cancel:task.cancel();await asyncio.sleep(0)
        else:pool.close()
        gate.set()
        with self.assertRaises((p.Fault,asyncio.CancelledError)):await task
        self.assertEqual(connection.closed,not finish_failure)
        self.assertEqual(pool._held,finish_failure)
        self.assertEqual(pool._uncertain,finish_failure)
        self.assertEqual(connection.finishes,1)
        self.assertEqual(self.driver.await_count,1)
    async def test_late_connect_after_close(self):await self.late()
    async def test_late_connect_after_cancel(self):await self.late(cancel=True)
    async def test_late_connect_after_cancel_with_failed_finish(self):await self.late(cancel=True,finish_failure=True)
    async def test_late_connect_after_close_with_failed_finish(self):await self.late(finish_failure=True)
    async def test_actual_driver_wrapper_fixed_kwargs_with_synthetic_driver(self):
        # Exercise original wrapper separately from the pool mock, never installed
        # driver network code. Import a fresh source module under test alias.
        module=types.ModuleType('pool_driver_test');module.__package__='cyomics_projects'
        sys.modules[module.__name__]=module
        exec(compile(MODULE_SOURCE,'synthetic_fresh_pool_import','exec'),module.__dict__)
        connect=AsyncMock(return_value=Connection());fake=types.ModuleType('psycopg');fake.AsyncConnection=SimpleNamespace(connect=connect)
        with patch.dict(sys.modules,{'psycopg':fake}):await module._connect('S'*64)
        kw=connect.call_args.kwargs
        self.assertEqual(kw,{'host':'172.19.0.2','hostaddr':'172.19.0.2','port':5432,'dbname':'cyomicsv2_business_dev','user':'cyomicsv2_raw_qc_worker_runtime','password':'S'*64,'connect_timeout':2,'require_auth':'scram-sha-256','sslmode':'disable','gssencmode':'disable','autocommit':True})

class DescriptorRevisionTests(unittest.IsolatedAsyncioTestCase):
    def synthetic_loader(self,stack,*,close_fail=(),unsafe_child=False,fail_child_open=False):
        import stat
        observation={'opened':[],'closed':[]};raw=secret()
        def opened(*args,**kwargs):
            if fail_child_open and observation['opened']:raise OSError('synthetic open failure')
            fd=10+len(observation['opened']);observation['opened'].append(fd);return fd
        def metadata(fd):
            return SimpleNamespace(st_mode=(stat.S_IFREG|0o600) if fd==15 else (stat.S_IFDIR|(0o777 if unsafe_child and fd==11 else 0o700)),
                                   st_uid=1000,st_dev=1,st_ino=fd,st_size=len(raw) if fd==15 else 0,st_nlink=1,st_mtime_ns=1,st_ctime_ns=2)
        def close(fd):
            observation['closed'].append(fd)
            if fd in close_fail:raise BaseException('SYNTHETIC_PRIVATE_CLOSE_TEXT')
        for name in ('O_DIRECTORY','O_CLOEXEC','O_NOFOLLOW','O_NONBLOCK'):stack.enter_context(patch.object(p.os,name,0x4000,create=True))
        stack.enter_context(patch.object(p.os,'open',side_effect=opened))
        stack.enter_context(patch.object(p.os,'fstat',side_effect=metadata))
        stack.enter_context(patch.object(p.os,'stat',return_value=metadata(15)))
        stack.enter_context(patch.object(p.os,'close',side_effect=close))
        stack.enter_context(patch.object(p.os,'read',side_effect=[raw,b'']))
        return observation
    async def test_WP01_child_is_owned_before_parent_close_and_no_numeric_retry(self):
        with ExitStack() as stack:
            observed=self.synthetic_loader(stack,close_fail=(10,))
            with self.assertRaises(p._SecretTeardownUnknown):p._load_password(1000)
        self.assertEqual(observed,{'opened':[10,11],'closed':[10,11]})
    async def test_ancestor_and_child_close_failures_each_attempted_once(self):
        with ExitStack() as stack:
            observed=self.synthetic_loader(stack,close_fail=(10,11))
            with self.assertRaises(p._SecretTeardownUnknown):p._load_password(1000)
        self.assertEqual(observed,{'opened':[10,11],'closed':[10,11]})
    async def test_rejected_child_metadata_cleans_child_and_parent(self):
        with ExitStack() as stack:
            observed=self.synthetic_loader(stack,unsafe_child=True)
            with self.assertRaises(p.Fault) as cm:p._load_password(1000)
            self.assertNotIsInstance(cm.exception,p._SecretTeardownUnknown)
        self.assertEqual(observed,{'opened':[10,11],'closed':[11,10]})
    async def test_child_open_failure_cleans_parent_once(self):
        with ExitStack() as stack:
            observed=self.synthetic_loader(stack,fail_child_open=True)
            with self.assertRaises(p.Fault) as cm:p._load_password(1000)
            self.assertNotIsInstance(cm.exception,p._SecretTeardownUnknown)
        self.assertEqual(observed,{'opened':[10],'closed':[10]})
    async def test_leaf_or_final_parent_teardown_unknown_never_retries(self):
        for fd in (14,15):
            with self.subTest(fd=fd),ExitStack() as stack:
                observed=self.synthetic_loader(stack,close_fail=(fd,))
                with self.assertRaises(p._SecretTeardownUnknown):p._load_password(1000)
                self.assertEqual(observed['opened'],list(range(10,16)))
                self.assertEqual(observed['closed'],[10,11,12,13,15,14])
    async def test_actual_loader_unknown_teardown_poison_prevents_repeated_opens(self):
        for fd in (10,14,15):
            with self.subTest(fd=fd),ExitStack() as stack:
                observed=self.synthetic_loader(stack,close_fail=(fd,))
                stack.enter_context(patch.object(p.os,'name','posix'))
                stack.enter_context(patch.object(p.os,'getuid',return_value=1000,create=True))
                stack.enter_context(patch.object(p.os,'geteuid',return_value=1000,create=True))
                stack.enter_context(patch.dict(p.os.environ,{},clear=True))
                connect=stack.enter_context(patch.object(p,'_connect',new_callable=AsyncMock))
                pool=p.WorkerPool(p.Config(1000))
                with self.assertRaises(p.Fault):await pool.getconn()
                opens=list(observed['opened'])
                with self.assertRaises(p.Fault):await pool.getconn()
                self.assertEqual(observed['opened'],opens)
                self.assertTrue(pool._held);self.assertTrue(pool._uncertain);self.assertEqual(pool._slots._value,0)
                connect.assert_not_called()
    async def test_clean_metadata_refusal_returns_preconnect_capacity(self):
        with ExitStack() as stack:
            self.synthetic_loader(stack,unsafe_child=True)
            stack.enter_context(patch.object(p.os,'name','posix'))
            stack.enter_context(patch.object(p.os,'getuid',return_value=1000,create=True))
            stack.enter_context(patch.object(p.os,'geteuid',return_value=1000,create=True))
            stack.enter_context(patch.dict(p.os.environ,{},clear=True))
            connect=stack.enter_context(patch.object(p,'_connect',new_callable=AsyncMock))
            pool=p.WorkerPool(p.Config(1000))
            with self.assertRaises(p.Fault):await pool.getconn()
            self.assertFalse(pool._held);self.assertFalse(pool._uncertain);self.assertEqual(pool._slots._value,1)
            connect.assert_not_called()

if __name__=='__main__':unittest.main(verbosity=2)
