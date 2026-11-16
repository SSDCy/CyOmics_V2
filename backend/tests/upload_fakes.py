"""Protocol doubles only: no PostgreSQL/RLS/persistence claim."""
import asyncio
from uuid import uuid4
from psycopg.pq import TransactionStatus as T
from fakes import A, B, PROJECT, SECOND, NOW, Cursor

class FilePool:
    def __init__(self):
        self.projects={PROJECT:A,SECOND:B};self.files=[];self.calls=[];self.released=[]
        self.locks={};self.connections=[];self.commit_lost=False;self.commit_rejected=False
        self.lock_pause=None;self.lock_entered=asyncio.Event()
    async def getconn(self):
        connection=FileConnection(self);self.connections.append(connection);return connection
    def release(self,c):
        self.released.append(c.subject)
        if c.transaction_status!=T.IDLE:c.finish()
    def close(self):
        for c in self.connections:c.finish()

class FileConnection:
    def __init__(self,p):
        self.pool=p;self.pgconn=self;self.transaction_status=T.IDLE;self.closed=False
        self.subject=None;self.pending=[];self.lock=None
    def unlock(self):
        if self.lock is not None and self.pool.locks.get(self.lock) is self:del self.pool.locks[self.lock]
        self.lock=None
    def finish(self):
        self.closed=True;self.transaction_status=T.UNKNOWN;self.pending.clear();self.subject=None;self.unlock()
    async def execute(self,sql,params=()):
        p=self.pool;p.calls.append((sql,params))
        if sql.startswith('BEGIN'):
            assert 'READ COMMITTED' in sql;self.transaction_status=T.INTRANS;return Cursor()
        if "set_config('app.user_id'" in sql:self.subject=params[0];return Cursor()
        if sql.startswith('SELECT pg_catalog.set_config'):return Cursor()
        if sql=='ROLLBACK':self.pending.clear();self.subject=None;self.transaction_status=T.IDLE;self.unlock();return Cursor()
        if sql=='COMMIT':
            if p.commit_rejected:self.transaction_status=T.INERROR;raise RuntimeError('synthetic commit rejected')
            p.files.extend(self.pending);self.pending.clear();self.subject=None;self.transaction_status=T.IDLE;self.unlock()
            if p.commit_lost:p.commit_lost=False;self.finish();raise ConnectionError('synthetic commit outcome unknown')
            return Cursor()
        if sql.startswith('SELECT project_id FROM'):
            return Cursor(one=(params[1],) if p.projects.get(params[1])==str(params[0])==self.subject else None)
        if 'pg_try_advisory_xact_lock' in sql:
            if params[0] in p.locks:return Cursor(one=(False,))
            p.locks[params[0]]=self;self.lock=params[0];p.lock_entered.set()
            if p.lock_pause is not None:await p.lock_pause.wait()
            return Cursor(one=(True,))
        rows=[row for row in p.files if p.projects.get(row[1])==self.subject]
        if sql.startswith('SELECT count(*)'):
            local=[r for r in rows if r[1]==params[0]]
            return Cursor(one=(len(rows),sum(r[5] for r in rows),len(local),sum(r[5] for r in local)))
        if sql.startswith('SELECT ') and 'FROM cyomics_app.raw_uploads' in sql:
            rows=[r for r in rows if r[1]==params[0]]
            if 'ORDER BY' in sql:return Cursor(many=[r[:9] for r in sorted(rows,key=lambda r:(r[8],r[0]),reverse=True)[:5]])
            index=2 if 'AND request_id=' in sql else 0
            row=next((r for r in rows if r[index]==params[1]),None)
            return Cursor(one=row if row is None or ',content FROM' in sql else row[:9])
        if sql.startswith('INSERT INTO cyomics_app.raw_uploads'):
            assert p.projects.get(params[0])==self.subject
            assert self.lock is not None and p.locks.get(self.lock) is self
            row=(uuid4(),*params[:6],'stored',NOW,bytes(params[6]))
            assert len(row)==10;self.pending.append(row);return Cursor(one=row[:9])
        raise AssertionError('Unexpected synthetic SQL')
