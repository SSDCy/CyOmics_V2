"""Offline protocol doubles. These do not simulate or prove PostgreSQL RLS."""
import asyncio
from datetime import datetime, timezone
from uuid import UUID
from psycopg.pq import TransactionStatus as T

A = '11111111-1111-4111-8111-111111111111'
B = '22222222-2222-4222-8222-222222222222'
WORKSPACE = UUID('33333333-3333-4333-8333-333333333333')
PROJECT = UUID('44444444-4444-4444-8444-444444444444')
SECOND = UUID('55555555-5555-4555-8555-555555555555')
NOW = datetime(2026, 11, 16, 1, 2, 3, 456789, timezone.utc)


def row(project_id=PROJECT, name='项目甲'):
    return (project_id,name,'description','species label','private',NOW,NOW)


class Cursor:
    def __init__(self, one=None, many=None):
        self.one, self.many = one, many
    async def fetchone(self): return self.one
    async def fetchall(self): return self.many or []


class Connection:
    def __init__(self, pool):
        self.pool = pool
        self.pgconn = self
        self.transaction_status = T.IDLE
        self.closed = False
        self.subject = None
        self.pending = []
        self.read_entered = self.commit_entered = False

    def finish(self):
        self.closed = True
        self.transaction_status = T.UNKNOWN
        self.subject = None
        self.pending.clear()

    async def execute(self, sql, params=()):
        self.pool.calls.append((sql,params))
        if sql.startswith('BEGIN'):
            self.transaction_status = T.INTRANS
            return Cursor()
        if "set_config('app.user_id'" in sql:
            self.subject = params[0]
            self.pool.subject_history.append(self.subject)
            return Cursor()
        if sql.startswith('SELECT pg_catalog.set_config'):
            return Cursor()
        if sql == 'ROLLBACK':
            self.pending.clear()
            self.subject = None
            self.transaction_status = T.IDLE
            return Cursor()
        if sql == 'COMMIT':
            self.commit_entered = True
            if self.pool.stall_commit:
                await asyncio.sleep(10)
            if self.pool.commit_rejected:
                self.transaction_status = T.INERROR
                raise RuntimeError('opaque internal commit failure')
            for item in self.pending:
                self.pool.rows.setdefault(self.subject, []).append(item)
            self.pending.clear()
            self.transaction_status = T.IDLE
            self.subject = None
            if self.pool.commit_lost:
                self.closed = True
                self.transaction_status = T.UNKNOWN
                raise ConnectionError('opaque internal response lost')
            return Cursor()
        if sql.startswith('SELECT workspace_id'):
            self.pool.workspace_reads += 1
            present = self.pool.workspace_exists or (self.pool.concurrent_workspace and self.pool.workspace_reads > 1)
            return Cursor(one=(WORKSPACE,) if present else None)
        if sql.startswith('INSERT INTO cyomics_app.workspaces'):
            if self.pool.concurrent_workspace:
                return Cursor()
            return Cursor(one=(WORKSPACE,))
        if sql.startswith('INSERT INTO cyomics_app.projects'):
            if self.pool.write_rejected:
                self.transaction_status = T.INERROR
                raise RuntimeError('opaque private column exception')
            item = row(name=params[-3])
            self.pending.append(item)
            return Cursor(one=item)
        if 'FROM cyomics_app.projects' in sql:
            self.read_entered = True
            if self.pool.stall_read:
                await asyncio.sleep(10)
            items = self.pool.rows.get(str(params[0]), [])
            if 'project_id=%s' in sql:
                return Cursor(one=next((item for item in items if item[0] == params[1]), None))
            return Cursor(many=items[:params[-1]])
        raise AssertionError('Unexpected fake-driver command')


class Pool:
    def __init__(self):
        self.rows = {A:[row()], B:[row(SECOND,'项目乙')]}
        self.calls = []
        self.subject_history = []
        self.release_subjects = []
        self.checkouts = 0
        self.connection = Connection(self)
        self.workspace_exists = True
        self.concurrent_workspace = False
        self.workspace_reads = 0
        self.write_rejected = self.commit_rejected = self.commit_lost = False
        self.stall_commit = self.stall_read = False

    async def getconn(self):
        self.checkouts += 1
        if self.connection.closed:
            self.connection = Connection(self)
        return self.connection

    def release(self, connection):
        self.release_subjects.append(connection.subject)
        if connection.transaction_status != T.IDLE:
            connection.finish()

    def close(self):
        self.connection.finish()
