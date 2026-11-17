"""Owner/transaction/unique-key protocol doubles, never proof of PostgreSQL RLS."""
import asyncio
import hashlib
from uuid import UUID
from psycopg.pq import TransactionStatus as T
from fakes import A, B, PROJECT, SECOND, NOW, Cursor

FILE = UUID('66666666-6666-4666-8666-666666666666')
OTHER_FILE = UUID('77777777-7777-4777-8777-777777777777')
RAW = b'\xef\xbb\xbfProtein IDs\tIntensity sample A\tLFQ intensity sample A\r\nP1; P2\t0\t\r\nP3\t1\t0\r\n'


class InlineTestRunner:
    """Synthetic tests only. MUST NOT be used as an ASGI/production runner."""
    def __init__(self):
        self.calls = 0
    async def run(self, operation, budget, *, key=None):
        self.calls += 1
        budget.check()
        return operation(lambda: False)


class ImportPool:
    def __init__(self, raw=RAW):
        self.projects = {PROJECT:A, SECOND:B}
        self.files = {}
        self.set_raw(FILE,PROJECT,raw)
        self.set_raw(OTHER_FILE,SECOND,raw)
        self.versions = {}; self.calls = []; self.connections = []; self.released = []
        self.locks = {}; self.insert_entered = asyncio.Event(); self.waiter_entered = asyncio.Event()
        self.pause_insert = None
        self.fail_insert = self.corrupt_return = False
        self.commit_mode = None
        self.on_insert = self.on_write_commit = None

    def set_raw(self,file_id,project_id,raw):
        self.files[file_id] = (file_id,project_id,'proteinGroups_txt',len(raw),hashlib.sha256(raw).hexdigest(),'stored',raw)

    async def getconn(self):
        c = ImportConnection(self); self.connections.append(c); return c

    def release(self,c):
        self.released.append(c)
        if c.transaction_status!=T.IDLE:
            c.finish()


class ImportConnection:
    def __init__(self,pool):
        self.pool=pool; self.pgconn=self; self.transaction_status=T.IDLE; self.closed=False
        self.subject=None; self.pending=None; self.key=None; self.lock=None

    def unlock(self):
        if self.key is not None and self.pool.locks.get(self.key) is self.lock:
            del self.pool.locks[self.key]; self.lock.set()
        self.key=self.lock=None

    def finish(self):
        self.closed=True; self.transaction_status=T.UNKNOWN; self.subject=None; self.pending=None; self.unlock()

    async def execute(self,sql,params=()):
        p=self.pool; p.calls.append((sql,params))
        if sql.startswith('BEGIN'):
            assert 'READ COMMITTED' in sql
            self.transaction_status=T.INTRANS; return Cursor()
        if "set_config('app.user_id'" in sql:
            self.subject=params[0]; return Cursor()
        if sql.startswith('SELECT pg_catalog.set_config'):
            return Cursor()
        if sql=='ROLLBACK':
            self.pending=None; self.subject=None; self.transaction_status=T.IDLE; self.unlock(); return Cursor()
        if sql=='COMMIT':
            if self.pending is not None:
                if p.commit_mode=='rejected':
                    self.transaction_status=T.INERROR; raise RuntimeError('synthetic private commit rejection')
                p.versions[self.key]=self.pending
                self.pending=None
                if p.on_write_commit is not None:p.on_write_commit()
                mode=p.commit_mode; p.commit_mode=None
            else:mode=None
            self.subject=None; self.transaction_status=T.IDLE; self.unlock()
            if mode in ('lost','cancelled'):
                self.finish()
                if mode=='cancelled':raise asyncio.CancelledError()
                raise ConnectionError('synthetic private committed reply lost')
            return Cursor()
        if sql.startswith('SELECT project_id FROM'):
            return Cursor(one=(params[1],) if p.projects.get(params[1])==str(params[0])==self.subject else None)
        if 'FROM cyomics_app.raw_uploads' in sql:
            row=p.files.get(params[1])
            if row is None or row[1]!=params[0] or p.projects.get(row[1])!=self.subject:return Cursor()
            return Cursor(one=row if ',content FROM' in sql else row[:6])
        if sql.startswith('SELECT ') and 'FROM cyomics_app.dataset_versions' in sql:
            row=p.versions.get((params[1],params[2],params[3]))
            if row is None or row[1]!=params[0] or p.projects.get(row[1])!=self.subject:return Cursor()
            return Cursor(one=row)
        if sql.startswith('INSERT INTO cyomics_app.dataset_versions'):
            assert p.projects.get(params[1])==self.subject and p.files[params[2]][1]==params[1]
            assert 'ON CONFLICT (file_id,parser_contract,config_sha256) DO NOTHING' in sql
            if p.fail_insert:raise RuntimeError('synthetic private storage failure')
            key=(params[2],params[3],params[4])
            if key in p.locks:
                p.waiter_entered.set(); await p.locks[key].wait()
            if key in p.versions:return Cursor()
            self.key=key; self.lock=asyncio.Event();p.locks[key]=self.lock
            self.pending=(*params,NOW); assert len(self.pending)==13
            p.insert_entered.set()
            if p.pause_insert is not None:await p.pause_insert.wait()
            if p.on_insert is not None:p.on_insert()
            if p.corrupt_return:
                row=list(self.pending);row[10]='{}';row[11]=hashlib.sha256(b'{}').hexdigest();return Cursor(one=tuple(row))
            return Cursor(one=self.pending)
        raise AssertionError('Unexpected synthetic import SQL')
